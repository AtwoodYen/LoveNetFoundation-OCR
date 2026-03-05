"""Base parser result.

Defines common fields and JSON/Markdown save logic.
"""

from __future__ import annotations

import json
import traceback
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from glmocr.utils.logging import get_logger

logger = get_logger(__name__)


class BaseParserResult(ABC):
    """Base parser result.

    Common interface: json_result, markdown_result, original_images; abstract save().
    """

    def __init__(
        self,
        json_result: Union[str, dict, list],
        markdown_result: Optional[str] = None,
        original_images: Optional[List[str]] = None,
        image_files: Optional[Dict[str, Any]] = None,
    ):
        """Initialize.

        Args:
            json_result: JSON result (string, dict, or list).
            markdown_result: Markdown result (optional).
            original_images: Original image paths.
            image_files: Mapping of ``filename`` → PIL Image for image-type
                regions, to be saved under ``imgs/`` during :meth:`save`.
        """
        if isinstance(json_result, str):
            try:
                self.json_result: Union[str, dict, list] = json.loads(json_result)
            except json.JSONDecodeError:
                self.json_result = json_result
        else:
            self.json_result = json_result

        self.markdown_result = markdown_result
        self.original_images = [
            str(Path(p).absolute()) for p in (original_images or [])
        ]
        self.image_files = image_files

    @abstractmethod
    def save(
        self,
        output_dir: Union[str, Path] = "./results",
        save_layout_visualization: bool = True,
    ) -> None:
        """Save result to disk. Subclasses implement layout vis etc."""
        pass

    def _save_json_and_markdown(self, output_dir: Union[str, Path]) -> None:
        """Save JSON and Markdown to output_dir (by first image name or 'result')."""
        output_dir = Path(output_dir).absolute()
        if self.original_images:
            image_path = Path(self.original_images[0])
            output_path = output_dir / image_path.stem
        else:
            output_path = output_dir / "result"

        output_path.mkdir(parents=True, exist_ok=True)
        base_name = output_path.name

        # JSON
        json_file = output_path / f"{base_name}.json"
        try:
            json_data = self.json_result
            if isinstance(json_data, str):
                try:
                    json_data = json.loads(json_data)
                except json.JSONDecodeError:
                    pass
            with open(json_file, "w", encoding="utf-8") as f:
                if isinstance(json_data, (dict, list)):
                    json.dump(json_data, f, ensure_ascii=False, indent=2)
                else:
                    f.write(str(json_data))
        except Exception as e:
            logger.warning("Failed to save JSON: %s", e)
            traceback.print_exc()

        # Markdown
        if self.markdown_result and self.markdown_result.strip():
            md_file = output_path / f"{base_name}.md"
            with open(md_file, "w", encoding="utf-8") as f:
                f.write(self.markdown_result)

        # Image files produced by the result formatter
        if self.image_files:
            imgs_dir = output_path / "imgs"
            imgs_dir.mkdir(parents=True, exist_ok=True)
            for filename, img in self.image_files.items():
                try:
                    img.save(imgs_dir / filename, quality=95)
                except Exception as e:
                    logger.warning("Failed to save image %s: %s", filename, e)
            self.image_files = None

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dict of the result.

        Useful for agents and programmatic consumers that need a structured
        representation without touching the file system.
        """
        d: dict = {
            "json_result": self.json_result,
            "markdown_result": self.markdown_result or "",
            "original_images": self.original_images,
        }
        # Include optional metadata set by MaaS mode.
        for attr in ("_usage", "_data_info", "_error"):
            val = getattr(self, attr, None)
            if val is not None:
                d[attr.lstrip("_")] = val
        return d

    def to_json(self, **kwargs: Any) -> str:
        """Serialise the result to a JSON string.

        Keyword arguments are forwarded to :func:`json.dumps`.
        """
        kwargs.setdefault("ensure_ascii", False)
        kwargs.setdefault("indent", 2)
        return json.dumps(self.to_dict(), **kwargs)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(images={len(self.original_images)})"
