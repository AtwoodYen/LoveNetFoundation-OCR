"""
捐獻袋 OCR 輸出規則處理模組

規則說明：
- 第一階段：過濾無效資料（雜訊、重複標記等）
- 第二階段：組合捐獻項目名稱並配對金額
- 第三階段：找出合計金額

輸出格式範例：
    課程推廣與發展：1000
    合計：1000
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.utils.logger import logger


@dataclass
class DonateItem:
    """捐獻項目"""
    name: str                      # 項目名稱（組合後）
    keywords: List[str]            # 組成關鍵字
    indices: List[int] = field(default_factory=list)   # 對應的 block indices
    y_values: List[int] = field(default_factory=list)  # 各 block 的 Y 軸值
    avg_y: int = 0                 # Y 軸平均值


@dataclass
class DonateMoney:
    """捐獻金額"""
    amount: str                    # 金額字串
    index: int                     # block index
    y: int                         # Y 軸值


@dataclass
class DonationResult:
    """捐獻結果"""
    item_name: str                 # 捐獻項目名稱
    amount: str                    # 金額
    total: str = ""                # 合計金額


# 定義四個捐獻項目的關鍵字
DONATE_ITEMS_CONFIG = [
    {
        "name": "課程推廣與發展",
        "keywords": ["課程", "推廣", "與", "發展"],
    },
    {
        "name": "媒體製作與傳播",
        "keywords": ["媒體", "製作", "與", "傳播"],
    },
    {
        "name": "基金會營運支出",
        "keywords": ["基金會", "營運", "支持"],  # 注意：原文是"支持"但組合成"支出"
    },
    {
        "name": "其他",
        "keywords": ["其他"],
    },
]


class DonationRulesProcessor:
    """捐獻袋 OCR 規則處理器"""

    def __init__(self, blocks: List[Dict[str, Any]]):
        """
        初始化處理器

        Args:
            blocks: Vision.json 中的 blocks 陣列，每個 block 包含:
                    - index: int
                    - text: str
                    - x: int
                    - y: int
                    - width: int
                    - height: int
        """
        self.original_blocks = blocks
        self.filtered_blocks: List[Dict[str, Any]] = []
        self.donate_items: List[DonateItem] = []
        self.donate_money: Optional[DonateMoney] = None
        self.total_amount: str = ""
        self.envelope_top_y: int = 0  # 信封上緣 Y 值（從"項目"或"金額"取得）
        self.heji_y: int = 0  # "合計" 的 Y 軸值
        self.heji_index: int = -1  # "合計" 的 index
        self.announcement_filtered_indices: set = set()  # 第四階段過濾掉的公告 indices
        # 第五階段：不同意揭露聲明
        self.agree_public_str: str = ""  # 合成的聲明文字
        self.agree_public_avg_y: int = 0  # 聲明文字的 Y 軸平均值
        self.agree_public_first_index: int = -1  # "本人" 的 index
        self.no_receipt_str: str = ""  # "不需要奉獻收據" 合成文字
        self.no_receipt_avg_y: int = 0  # 不需要收據的 Y 軸平均值
        # 第六階段：收據選項
        self.receipt_irs: Dict[str, Any] = {}  # 代上傳國稅局無紙本
        self.receipt_electronic: Dict[str, Any] = {}  # 電子收據
        self.receipt_paper: Dict[str, Any] = {}  # 年度紙本收據
        self.receipt_irs_checked: bool = False  # IRS 是否有勾選
        self.receipt_electronic_checked: bool = False  # 電子收據是否有勾選
        self.receipt_paper_checked: bool = False  # 紙本收據是否有勾選
        self.agree_public_checked: bool = False  # 不同意揭露是否有勾選
        # 第七階段：身分證字號
        self.id_card_label: str = ""  # "身分證字號:"
        self.id_card_number: str = ""  # 身分證字號（10碼）
        # 第八階段：奉獻者姓名
        self.donor_name_label: str = ""  # "奉獻者姓名:"
        self.donor_name: str = ""  # 奉獻者姓名
        self.found_name: bool = False  # 是否找到姓名的旗標
        # 第九階段：奉獻日期
        self.donation_date_label: str = ""  # "奉獻日期:"
        self.donation_date: str = ""  # 奉獻日期（如 "115年7月29日"）
        # 第十階段：奉獻收據抬頭
        self.receipt_title_label: str = ""  # "奉獻收據抬頭:"
        self.receipt_title: str = ""  # 收據抬頭
        self.found_title: bool = False  # 是否找到抬頭的旗標
        # 第十一階段：奉獻收據寄送地址
        self.mailing_address_label: str = ""  # "奉獻收據寄送地址:"
        self.mailing_address: str = ""  # 寄送地址
        # 第十二階段：聯絡電話
        self.telephone_label: str = ""  # "聯絡電話:"
        self.telephone_number: str = ""  # 電話號碼
        # 第十三階段：電子信箱
        self.mail_title: str = ""  # "電子信箱:"
        self.mail: str = ""  # 電子郵箱地址

    def process(self) -> Dict[str, Any]:
        """
        執行完整的處理流程

        Returns:
            處理結果，包含:
            - output_text: 最終輸出文字
            - donate_no: 捐獻項目結構化資料
            - filtered_blocks: 過濾後的 blocks
        """
        logger.info(f"開始處理捐獻袋 OCR 規則，共 {len(self.original_blocks)} 個區塊")

        # 第一階段：過濾無效資料
        self._stage1_filter()

        # 第二階段：組合捐獻項目名稱並配對金額
        self._stage2_match_items()

        # 第三階段：找出合計金額
        self._stage3_find_total()

        # 第四階段：過濾公告聲明
        self._stage4_filter_announcement()

        # 第五階段：不同意揭露聲明
        self._stage5_disagree_disclosure()

        # 第六階段：收據選項
        self._stage6_receipt_options()

        # 第七階段：身分證字號
        self._stage7_id_card()

        # 第八階段：奉獻者姓名
        self._stage8_donor_name()

        # 第九階段：奉獻日期
        self._stage9_donation_date()

        # 第十階段：奉獻收據抬頭
        self._stage10_receipt_title()

        # 第十一階段：奉獻收據寄送地址
        self._stage11_mailing_address()

        # 第十二階段：聯絡電話
        self._stage12_telephone()

        # 第十三階段：電子信箱
        self._stage13_email()

        # 產生輸出
        return self._generate_output()

    # ==================== 第一階段：過濾無效資料 ====================

    def _stage1_filter(self):
        """第一階段：過濾無效資料"""
        logger.info("=" * 60)
        logger.info("===== 第一階段：過濾無效資料 =====")
        logger.info("=" * 60)

        blocks = self.original_blocks.copy()
        filtered_indices = set()  # 要過濾掉的 index

        logger.info(f"[階段1] 開始處理，共 {len(blocks)} 個區塊")

        # 規則 1: "項目" 和 "金額" 不放入最終輸出，但記錄 Y 軸作為信封上緣
        logger.info("[規則1] 過濾 '項目' 和 '金額' 標題...")
        for block in blocks:
            text = block.get("text", "").strip()
            if text in ["項目", "金額"]:
                y = block.get("y", 0)
                if self.envelope_top_y == 0 or y < self.envelope_top_y:
                    self.envelope_top_y = y
                filtered_indices.add(block["index"])
                logger.info(f"  [過濾] '{text}' (index={block['index']}, y={y}) - 標題文字不輸出")

        # 規則 2, 3, 4 只對 index < 20 的資料處理
        early_blocks = [b for b in blocks if b["index"] < 20]
        logger.info(f"[規則2-4] 檢查前 20 個區塊（共 {len(early_blocks)} 個）...")

        # 規則 2: 長度為 4，且有 3 個相同的圓形字元（不含數字0）
        logger.info("[規則2] 檢查長度=4且有3個圓形字元的區塊...")
        for block in early_blocks:
            if block["index"] in filtered_indices:
                continue
            text = block.get("text", "")
            if len(text) == 4:
                if self._has_three_same_circles(text):
                    filtered_indices.add(block["index"])
                    logger.info(f"  [過濾] '{text}' (index={block['index']}) - 3個圓形字元")
                else:
                    logger.debug(f"  [保留] '{text}' (index={block['index']}) - 不符合規則2")

        # 規則 3: 長度為 4，且有 3 個字元相同（如 Q000, ooov, ○○○v）
        logger.info("[規則3] 檢查長度=4且有3個相同字元的區塊...")
        for block in early_blocks:
            if block["index"] in filtered_indices:
                continue
            text = block.get("text", "")
            if len(text) == 4:
                if self._has_three_same_chars(text):
                    filtered_indices.add(block["index"])
                    logger.info(f"  [過濾] '{text}' (index={block['index']}) - 3個相同字元")
                else:
                    logger.debug(f"  [保留] '{text}' (index={block['index']}) - 不符合規則3")

        # 規則 4: 連續 4 筆資料中有 3 筆內容相同
        logger.info("[規則4] 檢查連續4筆中有3筆相同...")
        for i in range(len(early_blocks) - 3):
            consecutive = early_blocks[i:i + 4]
            # 跳過已經被過濾的
            if any(b["index"] in filtered_indices for b in consecutive):
                continue

            texts = [b.get("text", "").strip() for b in consecutive]
            if self._has_three_same_in_four(texts):
                for b in consecutive:
                    filtered_indices.add(b["index"])
                logger.info(f"  [過濾] 連續4筆 {texts} (indices={[b['index'] for b in consecutive]})")

        # 建立過濾後的 blocks 列表
        self.filtered_blocks = [b for b in blocks if b["index"] not in filtered_indices]

        logger.info("-" * 40)
        logger.info(f"[階段1結果] 原 {len(blocks)} 個區塊 -> 過濾後 {len(self.filtered_blocks)} 個區塊")
        logger.info(f"[階段1結果] 被過濾的 indices: {sorted(filtered_indices)}")
        logger.info(f"[階段1結果] 信封上緣 Y 值: {self.envelope_top_y}")

    def _has_three_same_circles(self, text: str) -> bool:
        """
        檢查是否有 3 個相同的圓形字元 (○, o, O, 〇)

        注意：不包含數字 "0"，因為數字金額如 "3000" 不應被過濾
        """
        # 只檢查圓形符號，不包含數字 0
        circles = ["○", "o", "O", "〇"]
        count = sum(1 for c in text if c in circles)

        # 如果文字是純數字，不應該被過濾
        if text.replace(",", "").replace("，", "").isdigit():
            logger.debug(f"規則2跳過: '{text}' 是純數字金額，不過濾")
            return False

        result = count >= 3
        if result:
            logger.debug(f"規則2匹配: '{text}' 包含 {count} 個圓形字元")
        return result

    def _has_three_same_chars(self, text: str) -> bool:
        """
        檢查 4 字元中是否有 3 個相同

        注意：純數字金額（如 3000, 1000）不應被過濾
        """
        if len(text) != 4:
            return False

        # 如果文字是純數字，不應該被過濾（這是金額）
        if text.replace(",", "").replace("，", "").isdigit():
            logger.debug(f"規則3跳過: '{text}' 是純數字金額，不過濾")
            return False

        from collections import Counter
        counts = Counter(text)
        result = any(c >= 3 for c in counts.values())
        if result:
            logger.debug(f"規則3匹配: '{text}' 有 3 個相同字元")
        return result

    def _has_three_same_in_four(self, texts: List[str]) -> bool:
        """檢查 4 筆資料中是否有 3 筆內容相同"""
        if len(texts) != 4:
            return False
        from collections import Counter
        counts = Counter(texts)
        return any(c >= 3 for c in counts.values())

    # ==================== 第二階段：組合捐獻項目並配對金額 ====================

    def _stage2_match_items(self):
        """第二階段：組合捐獻項目名稱並配對金額"""
        logger.info("=" * 60)
        logger.info("===== 第二階段：組合捐獻項目並配對金額 =====")
        logger.info("=" * 60)

        # 2.1 找出捐獻項目名稱
        logger.info("[階段2.1] 尋找四個捐獻項目的關鍵字...")
        for config in DONATE_ITEMS_CONFIG:
            item = DonateItem(
                name=config["name"],
                keywords=config["keywords"],
            )

            # 找出每個關鍵字對應的 block
            for keyword in config["keywords"]:
                for block in self.filtered_blocks:
                    text = block.get("text", "").strip()
                    if text == keyword:
                        item.indices.append(block["index"])
                        item.y_values.append(block.get("y", 0))
                        logger.debug(f"找到關鍵字 '{keyword}' at index={block['index']}, y={block.get('y', 0)}")
                        break  # 每個關鍵字只取第一個匹配

            # 計算 Y 軸平均值
            if item.y_values:
                item.avg_y = int(sum(item.y_values) / len(item.y_values))
                logger.info(f"捐獻項目 '{item.name}': indices={item.indices}, avg_y={item.avg_y}")
            else:
                logger.warning(f"捐獻項目 '{item.name}' 未找到任何關鍵字")

            self.donate_items.append(item)

        # 2.2 找出捐獻金額 (Donate_Money)
        # 從過濾後的 blocks 中找第一個不是 "0000" 的數字
        self._find_donate_money()

        # 2.3 配對金額與捐獻項目（根據 Y 軸距離）
        if self.donate_money:
            self._match_money_to_item()

    def _find_donate_money(self):
        """找出捐獻金額（第一個非 0000 的數字）"""
        logger.info("[階段2.2] 尋找捐獻金額（第一個非0000的數字）...")
        logger.info(f"  搜尋範圍：{len(self.filtered_blocks)} 個過濾後的區塊")

        for block in self.filtered_blocks:
            text = block.get("text", "").strip()

            # 檢查是否為數字（移除可能的逗號）
            clean_text = text.replace(",", "").replace("，", "")

            # 跳過 "0000" 或全為 0 的
            if re.match(r"^0+$", clean_text):
                logger.debug(f"  [跳過] '{text}' (index={block['index']}) - 全為0")
                continue

            # 檢查是否為有效數字
            if re.match(r"^\d+$", clean_text):
                self.donate_money = DonateMoney(
                    amount=text,
                    index=block["index"],
                    y=block.get("y", 0),
                )
                logger.info(f"  [找到] 捐獻金額: {text} at index={block['index']}, y={block.get('y', 0)}")
                return
            else:
                logger.debug(f"  [跳過] '{text}' (index={block['index']}) - 非純數字")

        logger.warning("[階段2.2] 未找到捐獻金額！")
        logger.warning("  可能原因：所有數字區塊都被第一階段過濾掉了")

    def _match_money_to_item(self):
        """根據 Y 軸距離配對金額與捐獻項目"""
        logger.info("[階段2.3] 根據 Y 軸距離配對金額與捐獻項目...")

        if not self.donate_money:
            logger.warning("  [失敗] 沒有找到捐獻金額，無法配對")
            return

        money_y = self.donate_money.y
        logger.info(f"  金額 '{self.donate_money.amount}' 的 Y 軸: {money_y}")
        min_distance = float("inf")
        matched_item = None

        logger.info("  計算與各項目的 Y 軸距離：")
        for item in self.donate_items:
            if item.avg_y == 0:
                logger.debug(f"    - {item.name}: avg_y=0，跳過")
                continue
            distance = abs(money_y - item.avg_y)
            logger.info(f"    - {item.name}: avg_y={item.avg_y}, 距離={distance}")
            if distance < min_distance:
                min_distance = distance
                matched_item = item

        if matched_item:
            logger.info(f"  [配對成功] 金額 {self.donate_money.amount} -> '{matched_item.name}' (最小距離={min_distance})")
            self.matched_item_name = matched_item.name
        else:
            self.matched_item_name = ""
            logger.warning("  [配對失敗] 無法配對金額到任何捐獻項目")

    # ==================== 第三階段：找出合計金額 ====================

    def _stage3_find_total(self):
        """第三階段：找出合計金額"""
        logger.info("=" * 60)
        logger.info("===== 第三階段：找出合計金額 =====")
        logger.info("=" * 60)

        # 先找到 "合計" 的位置和 Y 軸
        logger.info("[階段3.1] 尋找 '合計' 標籤...")
        heji_block = None
        for block in self.filtered_blocks:
            text = block.get("text", "").strip()
            if text == "合計":
                heji_block = block
                self.heji_y = block.get("y", 0)
                self.heji_index = block["index"]
                logger.info(f"  [找到] '合計' at index={block['index']}, y={self.heji_y}")
                break

        if not heji_block:
            logger.warning("  [未找到] '合計' 標籤不存在")

        if not self.donate_money:
            logger.warning("[階段3.2] 沒有找到捐獻金額，無法計算合計")
            logger.warning("  可能原因：第一階段過濾掉了金額數字")
            return

        # 從 donate_money 的 index 往下找相同金額且與 "合計" Y軸距離 < 40px 的資料
        money_amount = self.donate_money.amount.replace(",", "").replace("，", "")
        start_index = self.donate_money.index + 1

        logger.info(f"[階段3.2] 尋找合計金額（與 '{money_amount}' 相同且 Y 距離 < 40px）...")
        logger.info(f"  從 index={start_index} 開始往後搜尋")

        for block in self.filtered_blocks:
            if block["index"] <= start_index:
                continue

            text = block.get("text", "").strip()
            clean_text = text.replace(",", "").replace("，", "")

            # 檢查是否為相同金額
            if clean_text == money_amount:
                y = block.get("y", 0)
                distance = abs(y - self.heji_y)

                logger.info(f"  [候選] '{text}' at index={block['index']}, y={y}, 與合計距離={distance}")

                if distance < 40:
                    self.total_amount = text
                    logger.info(f"  [找到] 合計金額: {text} (距離={distance} < 40px)")
                    return
                else:
                    logger.info(f"  [排除] 距離 {distance} >= 40px，不符合條件")

        # 沒找到符合條件的，使用 donate_money 的金額
        self.total_amount = self.donate_money.amount
        logger.info(f"[階段3結果] 未找到符合條件的合計金額，使用捐獻金額: {self.total_amount}")

    # ==================== 第四階段：過濾公告聲明 ====================

    # 公告聲明的關鍵字序列（用於識別法律聲明文字）
    ANNOUNCEMENT_KEYWORDS = [
        "依", "《", "財團", "法人", "法", "》", "第", "25", "條", "規定",
        ",", "本", "會", "需", "主動", "公開", "捐款", "者", "資訊", ";",
        "倘若", "您", "希望", "保密", "捐款", "資訊", ",", "請", "勾選",
        "「", "捐款", "不", "公開", "聲明", "」", "提供", "聲明", "書", "之",
        "捐款", "人", ",", "將", "依法", "規定", "公開", "揭露", "捐款", "者", "姓名"
    ]

    def _stage4_filter_announcement(self):
        """第四階段：過濾公告聲明（法律聲明文字不輸出）"""
        logger.info("===== 第四階段：過濾公告聲明 =====")

        if self.heji_index < 0:
            logger.info("未找到 '合計'，跳過第四階段")
            return

        # 從 "合計" 的 index 之後開始尋找公告聲明
        announcement_start_index = None
        announcement_end_index = None

        # 取得 "合計" 之後的所有 blocks
        blocks_after_heji = [
            b for b in self.filtered_blocks
            if b["index"] > self.heji_index
        ]

        if not blocks_after_heji:
            logger.info("'合計' 之後沒有更多資料")
            return

        # 尋找公告聲明的起始點（找到 "依" 開始）
        for block in blocks_after_heji:
            text = block.get("text", "").strip()
            if text == "依":
                announcement_start_index = block["index"]
                logger.info(f"找到公告聲明起始點 '依' at index={announcement_start_index}")
                break

        if announcement_start_index is None:
            # 嘗試尋找其他起始關鍵字
            for block in blocks_after_heji:
                text = block.get("text", "").strip()
                if text in ["《", "財團", "法人"]:
                    announcement_start_index = block["index"]
                    logger.info(f"找到公告聲明起始點 '{text}' at index={announcement_start_index}")
                    break

        if announcement_start_index is None:
            logger.info("未找到公告聲明起始點")
            return

        # 尋找 "姓名"，找到後將其之後的所有內容都標記為公告
        found_xingming = False
        for block in blocks_after_heji:
            if block["index"] < announcement_start_index:
                continue

            text = block.get("text", "").strip()

            # 標記從公告起始到結束的所有 blocks
            self.announcement_filtered_indices.add(block["index"])

            if text == "姓名":
                found_xingming = True
                logger.info(f"找到公告聲明結束標記 '姓名' at index={block['index']}")

        # 如果找到 "姓名"，將其之後的所有內容也過濾掉
        if found_xingming:
            for block in blocks_after_heji:
                if block["index"] >= announcement_start_index:
                    self.announcement_filtered_indices.add(block["index"])

        # 從 filtered_blocks 中移除公告內容
        original_count = len(self.filtered_blocks)
        self.filtered_blocks = [
            b for b in self.filtered_blocks
            if b["index"] not in self.announcement_filtered_indices
        ]

        filtered_count = original_count - len(self.filtered_blocks)
        logger.info(f"第四階段完成: 過濾了 {filtered_count} 個公告區塊")
        logger.info(f"公告區塊 indices: {sorted(self.announcement_filtered_indices)}")

    # ==================== 第五階段：不同意揭露聲明 ====================

    # 不同意揭露聲明的正確關鍵字序列
    DISAGREE_DISCLOSURE_KEYWORDS = [
        "本人", "在", "此", "聲明", "表示", "不", "同意", "將",
        "本人", "捐款", "姓名", "公開", "揭露"
    ]

    # 不需要收據的關鍵字
    NO_RECEIPT_KEYWORDS = ["不需要", "奉獻", "收據"]

    def _stage5_disagree_disclosure(self):
        """第五階段：處理不同意揭露聲明"""
        logger.info("===== 第五階段：不同意揭露聲明 =====")

        # 使用原始 blocks（包含被第四階段過濾的公告區塊）來搜尋
        # 因為聲明文字可能在公告區塊之後
        all_blocks = self.original_blocks

        # 5.1 尋找不同意揭露聲明
        self._find_disagree_disclosure(all_blocks)

        # 5.2 尋找不需要收據
        self._find_no_receipt(all_blocks)

    def _find_disagree_disclosure(self, all_blocks: List[Dict[str, Any]]):
        """尋找並組合不同意揭露聲明"""
        # 正確的聲明文字
        correct_text = "本人在此聲明表示不同意將本人捐款姓名公開揭露"

        # 尋找 "本人" 開始的位置
        start_index = None
        found_blocks = []
        y_values = []

        # 從所有 blocks 中尋找關鍵字序列
        keyword_index = 0
        found_jielu = False  # 是否找到 "揭露"
        found_buyaoao = False  # "揭露" 後是否找到 "不需要"

        for block in all_blocks:
            text = block.get("text", "").strip()

            # 如果已經找到 "揭露"，檢查後面是否有 "不需要"
            if found_jielu:
                if text == "不需要":
                    found_buyaoao = True
                    logger.info(f"在 '揭露' 後找到 '不需要'")
                # 遇到 "。" 或 "0" 或其他結束標記，停止搜尋
                if text in ["。", "0", ",", "，"]:
                    break
                continue

            # 尋找關鍵字序列
            if keyword_index < len(self.DISAGREE_DISCLOSURE_KEYWORDS):
                expected = self.DISAGREE_DISCLOSURE_KEYWORDS[keyword_index]

                # 允許一些 OCR 誤讀的容錯
                text_matches = (
                    text == expected or
                    (expected == "本人" and text in ["本人", "木人", "本入"]) or
                    (expected == "聲明" and text in ["聲明", "聲朋", "聲眀"]) or
                    (expected == "表示" and text in ["表示", "表亦"]) or
                    (expected == "同意" and text in ["同意", "同章"]) or
                    (expected == "捐款" and text in ["捐款", "損款"]) or
                    (expected == "姓名" and text in ["姓名", "姓召"]) or
                    (expected == "公開" and text in ["公開", "公闘"]) or
                    (expected == "揭露" and text in ["揭露", "揭路"])
                )

                if text_matches:
                    if start_index is None:
                        start_index = block["index"]
                        # 記錄第一個 "本人" 的 index
                        self.agree_public_first_index = block["index"]
                    found_blocks.append(block)
                    y_values.append(block.get("y", 0))
                    keyword_index += 1
                    logger.debug(f"找到關鍵字 '{expected}' (實際: '{text}') at index={block['index']}")

                    # 如果找到 "揭露"，標記並繼續搜尋看後面有沒有 "不需要"
                    if expected == "揭露":
                        found_jielu = True
                        logger.info(f"找到 '揭露' at index={block['index']}")

        # 檢查是否找到完整的聲明序列
        if keyword_index >= len(self.DISAGREE_DISCLOSURE_KEYWORDS):
            # 組合聲明文字
            self.agree_public_str = correct_text
            if not found_buyaoao:
                # 如果 "揭露" 後面沒有 "不需要"，加上句號
                self.agree_public_str += "。"

            # 計算 Y 軸平均值
            if y_values:
                self.agree_public_avg_y = int(sum(y_values) / len(y_values))

            logger.info(f"組合聲明文字: {self.agree_public_str}")
            logger.info(f"聲明 Y 軸平均值: {self.agree_public_avg_y}")
        else:
            logger.info(f"未找到完整的不同意揭露聲明 (找到 {keyword_index}/{len(self.DISAGREE_DISCLOSURE_KEYWORDS)} 個關鍵字)")

    def _find_no_receipt(self, all_blocks: List[Dict[str, Any]]):
        """尋找並組合不需要收據文字"""
        found_blocks = []
        y_values = []
        keyword_index = 0

        for block in all_blocks:
            text = block.get("text", "").strip()

            if keyword_index < len(self.NO_RECEIPT_KEYWORDS):
                expected = self.NO_RECEIPT_KEYWORDS[keyword_index]

                # 允許一些 OCR 誤讀的容錯
                text_matches = (
                    text == expected or
                    (expected == "不需要" and text in ["不需要", "不霄要"]) or
                    (expected == "奉獻" and text in ["奉獻", "奉獸"]) or
                    (expected == "收據" and text in ["收據", "收撼"])
                )

                if text_matches:
                    found_blocks.append(block)
                    y_values.append(block.get("y", 0))
                    keyword_index += 1
                    logger.debug(f"找到收據關鍵字 '{expected}' (實際: '{text}') at index={block['index']}")

        # 檢查是否找到完整的序列
        if keyword_index >= len(self.NO_RECEIPT_KEYWORDS):
            self.no_receipt_str = "不需要奉獻收據"

            # 計算 Y 軸平均值
            if y_values:
                self.no_receipt_avg_y = int(sum(y_values) / len(y_values))

            logger.info(f"組合收據文字: {self.no_receipt_str}")
            logger.info(f"收據 Y 軸平均值: {self.no_receipt_avg_y}")
        else:
            logger.info(f"未找到完整的不需要收據文字 (找到 {keyword_index}/{len(self.NO_RECEIPT_KEYWORDS)} 個關鍵字)")

    # ==================== 第六階段：收據選項 ====================

    # 要過濾的關鍵字（不輸出、不存於 temp.json）
    RECEIPT_FILTER_KEYWORDS = ["需要", "奉獻", "收", "據", "請", "務必", "填寫", "以下", "資料"]

    # IRS 關鍵字
    IRS_KEYWORDS = ["代", "上傳", "國稅局", "無", "紙", "本"]

    # 電子收據關鍵字
    ELECTRONIC_RECEIPT_KEYWORDS = ["電子", "收據"]

    # 紙本收據關鍵字
    PAPER_RECEIPT_KEYWORDS = ["年度", "紙", "本", "收據"]

    # 勾選標記字元（實際填寫的標記，不含空心圓）
    # 空心圓 ○, O, o, 〇 是印刷的選項標記，不是勾選
    # 實心圓、打勾、數字（OCR 可能誤讀 ✓ 為 6 或 8）才是實際勾選
    CHECKBOX_MARKS = ["6", "8", "①", "◎", "●", "☑", "✓", "√", "V", "v", "Y", "y"]

    def _stage6_receipt_options(self):
        """第六階段：處理收據選項"""
        logger.info("===== 第六階段：收據選項 =====")

        all_blocks = self.original_blocks

        # 6.1 尋找 IRS（代上傳國稅局無紙本）
        self._find_irs_option(all_blocks)

        # 6.2 尋找電子收據
        self._find_electronic_receipt(all_blocks)

        # 6.3 尋找紙本收據
        self._find_paper_receipt(all_blocks)

        # 6.4 檢查不同意揭露是否有勾選（根據規則 6）
        self._check_agree_public_checkbox(all_blocks)

    # 空心圓符號（印刷的選項標記，不是勾選）
    EMPTY_CIRCLE_MARKS = ["○", "O", "o", "〇", "0"]

    def _is_checkbox_mark(self, text: str) -> bool:
        """
        檢查文字是否為勾選標記

        注意：空心圓 ○, O, o, 〇, 0 是印刷的選項標記，不算勾選
        """
        text = text.strip()

        # 排除空心圓（印刷的選項標記）
        if text in self.EMPTY_CIRCLE_MARKS:
            return False

        # 檢查是否在勾選標記列表中
        if text in self.CHECKBOX_MARKS:
            return True

        return False

    def _is_chinese(self, char: str) -> bool:
        """檢查是否為中文字元"""
        if len(char) != 1:
            return False
        code = ord(char)
        return (0x4E00 <= code <= 0x9FFF or  # CJK Unified Ideographs
                0x3400 <= code <= 0x4DBF or  # CJK Unified Ideographs Extension A
                0xF900 <= code <= 0xFAFF)    # CJK Compatibility Ideographs

    def _check_checkbox(self, all_blocks: List[Dict[str, Any]], first_keyword_index: int,
                        first_keyword_x: int, combined_avg_y: int) -> bool:
        """
        檢查是否有勾選標記（使用座標範圍搜尋，不只是 index-1）

        注意：座標已互換（x = 垂直位置，y = 水平位置）
        在 RTL（右到左）閱讀的表單中，勾選標記在選項文字的右邊（較高的 X 值）

        搜尋邏輯：
        1. 搜尋所有區塊中的勾選標記字元
        2. 檢查是否在同一行（Y 距離 < 100px）
        3. 檢查是否在選項文字的右邊且距離合理（X 距離 < 500px）
        4. 選擇最近的有效勾選標記

        Args:
            all_blocks: 所有區塊
            first_keyword_index: 第一個關鍵字的 index
            first_keyword_x: 第一個關鍵字的 X 座標（實際是垂直位置）
            combined_avg_y: 合併文字的 Y 軸平均值（實際是水平位置）

        Returns:
            是否有勾選
        """
        logger.debug(f"  勾選檢查: keyword index={first_keyword_index}, x={first_keyword_x}, avg_y={combined_avg_y}")

        # 方法1：先檢查 index-1 的區塊（傳統方式）
        prev_block = None
        for block in all_blocks:
            if block["index"] == first_keyword_index - 1:
                prev_block = block
                break

        if prev_block:
            prev_text = prev_block.get("text", "").strip()
            prev_x = prev_block.get("x", 0)
            prev_y = prev_block.get("y", 0)

            logger.debug(f"  勾選檢查(index-1): prev='{prev_text}' (index={prev_block['index']}, x={prev_x}, y={prev_y})")

            if self._is_checkbox_mark(prev_text):
                y_distance = abs(prev_y - combined_avg_y)
                x_distance = abs(prev_x - first_keyword_x)
                if y_distance <= 100 and x_distance <= 500:
                    logger.info(f"  [勾選成功] 在 index-1 找到勾選標記 '{prev_text}' (x_dist={x_distance}, y_dist={y_distance})")
                    return True
            else:
                logger.debug(f"  勾選檢查(index-1): '{prev_text}' 不是勾選標記字元")

        # 方法2：在座標範圍內搜尋勾選標記
        logger.debug(f"  勾選檢查: 開始座標範圍搜尋...")

        candidates = []
        for block in all_blocks:
            text = block.get("text", "").strip()
            block_x = block.get("x", 0)
            block_y = block.get("y", 0)
            block_index = block.get("index", -1)

            # 跳過已經是關鍵字的區塊或之後的區塊
            if block_index >= first_keyword_index:
                continue

            # 檢查是否為勾選標記
            if not self._is_checkbox_mark(text):
                continue

            # 檢查 Y 軸距離（垂直位置/同一行）是否在 100px 內
            y_distance = abs(block_y - combined_avg_y)
            if y_distance > 100:
                logger.debug(f"    候選 '{text}' (index={block_index}): Y 距離 {y_distance}px > 100px，跳過")
                continue

            # 檢查 X 軸距離（水平位置）
            # 勾選標記應該在選項文字的右邊（較高的 X 值）
            x_diff = block_x - first_keyword_x
            if x_diff <= 0:
                # 勾選標記在關鍵字左邊（較低的 X 值），不太可能是這個選項的勾選
                logger.debug(f"    候選 '{text}' (index={block_index}): X={block_x} <= keyword_x={first_keyword_x}，在左邊，跳過")
                continue

            if x_diff > 500:
                # 太遠了
                logger.debug(f"    候選 '{text}' (index={block_index}): X 距離 {x_diff}px > 500px，太遠，跳過")
                continue

            # 找到有效的候選
            candidates.append({
                "text": text,
                "index": block_index,
                "x": block_x,
                "y": block_y,
                "x_diff": x_diff,
                "y_distance": y_distance,
            })
            logger.debug(f"    候選 '{text}' (index={block_index}): x_diff={x_diff}, y_dist={y_distance} - 有效候選")

        if not candidates:
            logger.debug(f"  勾選檢查: 沒有找到任何有效的勾選標記候選")
            return False

        # 選擇最近的候選（優先 X 距離，其次 Y 距離）
        best = min(candidates, key=lambda c: (c["x_diff"], c["y_distance"]))
        logger.info(f"  [勾選成功] 座標搜尋找到勾選標記 '{best['text']}' at index={best['index']} (x_diff={best['x_diff']}, y_dist={best['y_distance']})")
        return True

    def _find_irs_option(self, all_blocks: List[Dict[str, Any]]):
        """尋找 IRS（代上傳國稅局無紙本）選項"""
        found_blocks = []
        y_values = []
        first_x = None
        first_index = None
        keyword_index = 0

        for block in all_blocks:
            text = block.get("text", "").strip()

            if keyword_index < len(self.IRS_KEYWORDS):
                expected = self.IRS_KEYWORDS[keyword_index]

                if text == expected:
                    if first_index is None:
                        first_index = block["index"]
                        first_x = block.get("x", 0)
                    found_blocks.append(block)
                    y_values.append(block.get("y", 0))
                    keyword_index += 1
                    logger.debug(f"找到 IRS 關鍵字 '{expected}' at index={block['index']}")

        if keyword_index >= len(self.IRS_KEYWORDS):
            combined_text = "代上傳國稅局無紙本"
            avg_y = int(sum(y_values) / len(y_values)) if y_values else 0

            self.receipt_irs = {
                "text": combined_text,
                "first_x": first_x,
                "avg_y": avg_y,
                "first_index": first_index,
            }

            # 檢查是否有勾選
            self.receipt_irs_checked = self._check_checkbox(all_blocks, first_index, first_x, avg_y)

            logger.info(f"IRS 選項: {combined_text}, first_x={first_x}, avg_y={avg_y}, checked={self.receipt_irs_checked}")
        else:
            logger.info(f"未找到完整的 IRS 選項 (找到 {keyword_index}/{len(self.IRS_KEYWORDS)} 個關鍵字)")

    def _find_electronic_receipt(self, all_blocks: List[Dict[str, Any]]):
        """尋找電子收據選項"""
        found_blocks = []
        y_values = []
        first_x = None
        first_index = None
        keyword_index = 0

        for block in all_blocks:
            text = block.get("text", "").strip()

            if keyword_index < len(self.ELECTRONIC_RECEIPT_KEYWORDS):
                expected = self.ELECTRONIC_RECEIPT_KEYWORDS[keyword_index]

                if text == expected:
                    if first_index is None:
                        first_index = block["index"]
                        first_x = block.get("x", 0)
                    found_blocks.append(block)
                    y_values.append(block.get("y", 0))
                    keyword_index += 1
                    logger.debug(f"找到電子收據關鍵字 '{expected}' at index={block['index']}")

        if keyword_index >= len(self.ELECTRONIC_RECEIPT_KEYWORDS):
            combined_text = "電子收據"
            avg_y = int(sum(y_values) / len(y_values)) if y_values else 0

            self.receipt_electronic = {
                "text": combined_text,
                "first_x": first_x,
                "avg_y": avg_y,
                "first_index": first_index,
            }

            # 檢查是否有勾選
            self.receipt_electronic_checked = self._check_checkbox(all_blocks, first_index, first_x, avg_y)

            logger.info(f"電子收據選項: {combined_text}, first_x={first_x}, avg_y={avg_y}, checked={self.receipt_electronic_checked}")
        else:
            logger.info(f"未找到完整的電子收據選項 (找到 {keyword_index}/{len(self.ELECTRONIC_RECEIPT_KEYWORDS)} 個關鍵字)")

    def _find_paper_receipt(self, all_blocks: List[Dict[str, Any]]):
        """尋找紙本收據選項"""
        found_blocks = []
        y_values = []
        first_x = None
        first_index = None
        keyword_index = 0

        for block in all_blocks:
            text = block.get("text", "").strip()

            if keyword_index < len(self.PAPER_RECEIPT_KEYWORDS):
                expected = self.PAPER_RECEIPT_KEYWORDS[keyword_index]

                if text == expected:
                    if first_index is None:
                        first_index = block["index"]
                        first_x = block.get("x", 0)
                    found_blocks.append(block)
                    y_values.append(block.get("y", 0))
                    keyword_index += 1
                    logger.debug(f"找到紙本收據關鍵字 '{expected}' at index={block['index']}")

        if keyword_index >= len(self.PAPER_RECEIPT_KEYWORDS):
            combined_text = "年度紙本收據"
            avg_y = int(sum(y_values) / len(y_values)) if y_values else 0

            self.receipt_paper = {
                "text": combined_text,
                "first_x": first_x,
                "avg_y": avg_y,
                "first_index": first_index,
            }

            # 檢查是否有勾選
            self.receipt_paper_checked = self._check_checkbox(all_blocks, first_index, first_x, avg_y)

            logger.info(f"紙本收據選項: {combined_text}, first_x={first_x}, avg_y={avg_y}, checked={self.receipt_paper_checked}")
        else:
            logger.info(f"未找到完整的紙本收據選項 (找到 {keyword_index}/{len(self.PAPER_RECEIPT_KEYWORDS)} 個關鍵字)")

    def _check_agree_public_checkbox(self, all_blocks: List[Dict[str, Any]]):
        """
        檢查不同意揭露是否有勾選（規則 6）

        只要 IRS、電子收據、紙本收據其中一個有勾選，
        且 "本人" 前一個區塊是勾選標記，
        則輸出 "本人在此聲明表示不同意將本人捐款姓名公開揭露。"
        """
        # 檢查是否有任何收據選項被勾選
        any_receipt_checked = (
            self.receipt_irs_checked or
            self.receipt_electronic_checked or
            self.receipt_paper_checked
        )

        if not any_receipt_checked:
            logger.info("沒有任何收據選項被勾選，跳過不同意揭露檢查")
            return

        if self.agree_public_first_index < 0:
            logger.info("未找到 '本人' 區塊，跳過不同意揭露檢查")
            return

        # 尋找 "本人" 前一個區塊
        prev_block = None
        for block in all_blocks:
            if block["index"] == self.agree_public_first_index - 1:
                prev_block = block
                break

        if prev_block is None:
            logger.info("未找到 '本人' 前一個區塊")
            return

        prev_text = prev_block.get("text", "").strip()

        if self._is_checkbox_mark(prev_text):
            self.agree_public_checked = True
            logger.info(f"不同意揭露有勾選: 前一區塊 '{prev_text}' at index={prev_block['index']}")
        else:
            logger.info(f"不同意揭露未勾選: 前一區塊 '{prev_text}' 不是勾選標記")

    # ==================== 第七階段：身分證字號 ====================

    # 身分證字號標籤關鍵字
    ID_CARD_KEYWORDS = ["身分", "證", "字號", ":"]

    def _stage7_id_card(self):
        """第七階段：處理身分證字號"""
        logger.info("===== 第七階段：身分證字號 =====")

        all_blocks = self.original_blocks

        # 尋找身分證字號標籤
        found_blocks = []
        keyword_index = 0
        last_found_index = -1

        for block in all_blocks:
            text = block.get("text", "").strip()

            if keyword_index < len(self.ID_CARD_KEYWORDS):
                expected = self.ID_CARD_KEYWORDS[keyword_index]

                # 允許冒號的多種形式
                text_matches = (
                    text == expected or
                    (expected == ":" and text in [":", "：", ";"])
                )

                if text_matches:
                    found_blocks.append(block)
                    last_found_index = block["index"]
                    keyword_index += 1
                    logger.debug(f"找到身分證關鍵字 '{expected}' (實際: '{text}') at index={block['index']}")

        if keyword_index >= len(self.ID_CARD_KEYWORDS):
            self.id_card_label = "身分證字號:"
            logger.info(f"找到身分證標籤，最後一個關鍵字 index={last_found_index}")

            # 尋找下一個 index 的身分證字號
            next_block = None
            for block in all_blocks:
                if block["index"] == last_found_index + 1:
                    next_block = block
                    break

            if next_block:
                id_number = next_block.get("text", "").strip()
                logger.info(f"下一個區塊內容: '{id_number}' (長度={len(id_number)})")

                # 檢查是否為有效的身分證字號格式
                if self._is_valid_id_card(id_number):
                    self.id_card_number = id_number
                    logger.info(f"找到有效身分證字號: {id_number}")
                else:
                    logger.info(f"'{id_number}' 不是有效的身分證字號格式")
            else:
                logger.info("未找到身分證字號（下一個區塊不存在）")
        else:
            logger.info(f"未找到完整的身分證標籤 (找到 {keyword_index}/{len(self.ID_CARD_KEYWORDS)} 個關鍵字)")

    def _is_valid_id_card(self, text: str) -> bool:
        """
        檢查是否為有效的台灣身分證字號格式

        格式：1個英文字母 + 9個數字 = 共10碼
        """
        if len(text) != 10:
            return False

        # 第一個字元必須是英文字母
        first_char = text[0].upper()
        if not first_char.isalpha():
            return False

        # 後面9個字元必須是數字
        remaining = text[1:]
        if not remaining.isdigit():
            return False

        return True

    # ==================== 第八階段：奉獻者姓名 ====================

    # 奉獻者姓名標籤關鍵字
    DONOR_NAME_KEYWORDS = ["奉獻", "者", "姓名", ":"]

    def _stage8_donor_name(self):
        """第八階段：處理奉獻者姓名"""
        logger.info("=" * 60)
        logger.info("===== 第八階段：奉獻者姓名 =====")
        logger.info("=" * 60)

        all_blocks = self.original_blocks

        # 建立 index 到 block 的映射，方便查找
        index_to_block = {b["index"]: b for b in all_blocks}

        # 尋找奉獻者姓名標籤（必須是連續的關鍵字）
        logger.info("[階段8] 尋找 '奉獻者姓名:' 標籤（連續關鍵字）...")

        fengxian_index = -1  # "奉獻" 的 index
        colon_index = -1  # ":" 的 index

        # 先找所有 "奉獻" 的位置
        fengxian_candidates = []
        for block in all_blocks:
            if block.get("text", "").strip() == "奉獻":
                fengxian_candidates.append(block["index"])

        logger.info(f"  找到 {len(fengxian_candidates)} 個 '奉獻' 候選: {fengxian_candidates}")

        # 對每個 "奉獻" 候選，檢查後面是否有連續的 "者", "姓名", ":"
        for fengxian_idx in fengxian_candidates:
            logger.debug(f"  檢查候選 '奉獻' at index={fengxian_idx}...")

            # 檢查 index+1 是否為 "者"
            zhe_block = index_to_block.get(fengxian_idx + 1)
            if not zhe_block or zhe_block.get("text", "").strip() != "者":
                logger.debug(f"    index+1 不是 '者'，跳過")
                continue

            # 檢查 index+2 是否為 "姓名"
            xingming_block = index_to_block.get(fengxian_idx + 2)
            if not xingming_block or xingming_block.get("text", "").strip() != "姓名":
                logger.debug(f"    index+2 不是 '姓名'，跳過")
                continue

            # 檢查 index+3 是否為 ":"
            colon_block = index_to_block.get(fengxian_idx + 3)
            if not colon_block or colon_block.get("text", "").strip() not in [":", "：", ";"]:
                logger.debug(f"    index+3 不是 ':'，跳過")
                continue

            # 找到了連續的 "奉獻者姓名:"
            fengxian_index = fengxian_idx
            colon_index = fengxian_idx + 3
            logger.info(f"  [找到] 連續的 '奉獻者姓名:' 標籤: indices=[{fengxian_idx}, {fengxian_idx+1}, {fengxian_idx+2}, {fengxian_idx+3}]")
            break

        if fengxian_index < 0:
            logger.warning("[階段8] 未找到連續的 '奉獻者姓名:' 標籤")
            return

        self.donor_name_label = "奉獻者姓名:"
        logger.info(f"  奉獻 index={fengxian_index}, : index={colon_index}")

        # 方法一：往前找姓名（從 "奉獻" 的前一個 index 開始）
        self._find_donor_name_backwards(index_to_block, fengxian_index)

        # 方法二：如果往前沒找到，往後找
        if not self.found_name:
            self._find_donor_name_forwards(all_blocks, index_to_block, colon_index)

    def _find_donor_name_backwards(self, index_to_block: Dict[int, Dict], fengxian_index: int):
        """
        往前找奉獻者姓名

        從 "奉獻" 的前一個 index 開始往前找，
        直到找到 ":" 或長度為 10 的身分證字號才停止，
        然後合併 ":" 的下一個 index 到 "奉獻" 前一個 index 的所有內容
        """
        if fengxian_index <= 0:
            logger.info("奉獻 index 太小，無法往前找")
            return

        # 從 "奉獻" 的前一個 index 開始往前找
        current_index = fengxian_index - 1
        stop_index = -1  # 停止位置（":" 或身分證字號的 index）

        while current_index >= 0:
            block = index_to_block.get(current_index)
            if block is None:
                current_index -= 1
                continue

            text = block.get("text", "").strip()

            # 檢查是否為 ":"
            if text in [":", "：", ";"]:
                stop_index = current_index
                logger.debug(f"往前找到 ':' at index={current_index}")
                break

            # 檢查是否為身分證字號（長度 10，第一個是英文，後 9 個是數字）
            if self._is_valid_id_card(text):
                stop_index = current_index
                logger.debug(f"往前找到身分證字號 '{text}' at index={current_index}")
                break

            current_index -= 1

        if stop_index < 0:
            logger.info("往前找沒有找到 ':' 或身分證字號")
            return

        # 合併從 stop_index + 1 到 fengxian_index - 1 的所有內容
        name_parts = []
        for idx in range(stop_index + 1, fengxian_index):
            block = index_to_block.get(idx)
            if block:
                text = block.get("text", "").strip()
                if text:
                    name_parts.append(text)

        if name_parts:
            self.donor_name = "".join(name_parts)
            self.found_name = True
            logger.info(f"往前找到奉獻者姓名: {self.donor_name}")
        else:
            logger.info("往前找沒有找到姓名內容")

    def _find_donor_name_forwards(self, all_blocks: List[Dict], index_to_block: Dict[int, Dict], colon_index: int):
        """
        往後找奉獻者姓名

        從 ":" 的下一個 index 開始往後找，
        直到找到下一個 "奉獻" 才停止，
        "奉獻" 不加入姓名中
        """
        if colon_index < 0:
            logger.info("沒有找到 ':'，無法往後找")
            return

        name_parts = []
        current_index = colon_index + 1

        # 取得最大 index
        max_index = max(b["index"] for b in all_blocks) if all_blocks else 0

        while current_index <= max_index:
            block = index_to_block.get(current_index)
            if block is None:
                current_index += 1
                continue

            text = block.get("text", "").strip()

            # 如果找到 "奉獻"，停止（不加入）
            if text == "奉獻":
                logger.debug(f"往後找到 '奉獻' at index={current_index}，停止")
                break

            # 加入姓名
            if text:
                name_parts.append(text)
                logger.debug(f"往後找到姓名部分 '{text}' at index={current_index}")

            current_index += 1

        if name_parts:
            self.donor_name = "".join(name_parts)
            self.found_name = True
            logger.info(f"往後找到奉獻者姓名: {self.donor_name}")
        else:
            logger.info("往後找沒有找到姓名內容")

    # ==================== 第九階段：奉獻日期 ====================

    # 奉獻日期標籤關鍵字
    DONATION_DATE_KEYWORDS = ["奉獻", "日期", ":"]

    # 日期相關標記
    DATE_MARKERS = ["年", "月", "日"]

    def _stage9_donation_date(self):
        """第九階段：處理奉獻日期"""
        logger.info("===== 第九階段：奉獻日期 =====")

        all_blocks = self.original_blocks

        # 建立 index 到 block 的映射
        index_to_block = {b["index"]: b for b in all_blocks}

        # 尋找奉獻日期標籤
        keyword_index = 0
        colon_index = -1

        for block in all_blocks:
            text = block.get("text", "").strip()

            if keyword_index < len(self.DONATION_DATE_KEYWORDS):
                expected = self.DONATION_DATE_KEYWORDS[keyword_index]

                # 允許冒號的多種形式
                text_matches = (
                    text == expected or
                    (expected == ":" and text in [":", "：", ";"])
                )

                if text_matches:
                    if expected == ":":
                        colon_index = block["index"]
                    keyword_index += 1
                    logger.debug(f"找到奉獻日期關鍵字 '{expected}' (實際: '{text}') at index={block['index']}")

        if keyword_index < len(self.DONATION_DATE_KEYWORDS):
            logger.info(f"未找到完整的奉獻日期標籤 (找到 {keyword_index}/{len(self.DONATION_DATE_KEYWORDS)} 個關鍵字)")
            return

        self.donation_date_label = "奉獻日期:"
        logger.info(f"找到奉獻日期標籤，: index={colon_index}")

        # 從 ":" 的下一個 index 開始找日期
        self._find_donation_date(index_to_block, colon_index, all_blocks)

    def _find_donation_date(self, index_to_block: Dict[int, Dict], colon_index: int, all_blocks: List[Dict]):
        """
        尋找奉獻日期

        日期格式可能是：
        - "115", "年", "7", "月", "29", "日" -> "115年7月29日"
        - "2024", "年", "7", "月", "29", "日" -> "2024年7月29日"
        - "115/7/29" -> 直接使用
        """
        if colon_index < 0:
            logger.info("沒有找到 ':'，無法尋找日期")
            return

        date_parts = []
        current_index = colon_index + 1
        max_index = max(b["index"] for b in all_blocks) if all_blocks else 0

        # 持續尋找直到組成完整日期或遇到非日期內容
        found_year = False
        found_month = False
        found_day = False

        while current_index <= max_index:
            block = index_to_block.get(current_index)
            if block is None:
                current_index += 1
                continue

            text = block.get("text", "").strip()

            # 檢查是否為日期的一部分
            is_date_part = False

            # 數字（年份、月份、日期）
            if text.isdigit():
                is_date_part = True
                date_parts.append(text)
                logger.debug(f"找到日期數字 '{text}' at index={current_index}")

            # 年、月、日 標記
            elif text in self.DATE_MARKERS:
                is_date_part = True
                date_parts.append(text)
                if text == "年":
                    found_year = True
                elif text == "月":
                    found_month = True
                elif text == "日":
                    found_day = True
                logger.debug(f"找到日期標記 '{text}' at index={current_index}")

            # 斜線日期格式（如 115/7/29 或 2024/7/29）
            elif "/" in text and any(c.isdigit() for c in text):
                is_date_part = True
                date_parts.append(text)
                # 斜線格式視為完整日期
                found_year = found_month = found_day = True
                logger.debug(f"找到斜線日期 '{text}' at index={current_index}")

            # 如果不是日期部分，停止尋找
            if not is_date_part:
                logger.debug(f"遇到非日期內容 '{text}'，停止尋找")
                break

            # 如果已經找到完整的 年月日，停止
            if found_year and found_month and found_day:
                current_index += 1
                # 檢查是否還有更多日期內容（可能還有其他字元）
                next_block = index_to_block.get(current_index)
                if next_block:
                    next_text = next_block.get("text", "").strip()
                    # 如果下一個不是日期相關，就停止
                    if not (next_text.isdigit() or next_text in self.DATE_MARKERS or "/" in next_text):
                        break
                else:
                    break
                continue

            current_index += 1

        if date_parts:
            self.donation_date = "".join(date_parts)
            logger.info(f"找到奉獻日期: {self.donation_date}")
        else:
            logger.info("未找到奉獻日期內容")

    # ==================== 第十階段：奉獻收據抬頭 ====================

    # 奉獻收據抬頭標籤關鍵字
    RECEIPT_TITLE_KEYWORDS = ["奉獻", "收據", "抬頭", ":"]

    def _stage10_receipt_title(self):
        """第十階段：處理奉獻收據抬頭"""
        logger.info("=" * 60)
        logger.info("===== 第十階段：奉獻收據抬頭 =====")
        logger.info("=" * 60)

        all_blocks = self.original_blocks

        # 建立 index 到 block 的映射
        index_to_block = {b["index"]: b for b in all_blocks}

        # 尋找奉獻收據抬頭標籤（必須是連續的關鍵字）
        logger.info("[階段10] 尋找 '奉獻收據抬頭:' 標籤（連續關鍵字）...")

        fengxian_index = -1  # "奉獻" 的 index
        colon_index = -1  # ":" 的 index

        # 先找所有 "奉獻" 的位置
        fengxian_candidates = []
        for block in all_blocks:
            if block.get("text", "").strip() == "奉獻":
                fengxian_candidates.append(block["index"])

        logger.info(f"  找到 {len(fengxian_candidates)} 個 '奉獻' 候選: {fengxian_candidates}")

        # 對每個 "奉獻" 候選，檢查後面是否有連續的 "收據", "抬頭", ":"
        for fengxian_idx in fengxian_candidates:
            logger.debug(f"  檢查候選 '奉獻' at index={fengxian_idx}...")

            # 檢查 index+1 是否為 "收據"
            shouju_block = index_to_block.get(fengxian_idx + 1)
            if not shouju_block or shouju_block.get("text", "").strip() != "收據":
                logger.debug(f"    index+1 不是 '收據'，跳過")
                continue

            # 檢查 index+2 是否為 "抬頭"
            taitou_block = index_to_block.get(fengxian_idx + 2)
            if not taitou_block or taitou_block.get("text", "").strip() != "抬頭":
                logger.debug(f"    index+2 不是 '抬頭'，跳過")
                continue

            # 檢查 index+3 是否為 ":"
            colon_block = index_to_block.get(fengxian_idx + 3)
            if not colon_block or colon_block.get("text", "").strip() not in [":", "：", ";"]:
                logger.debug(f"    index+3 不是 ':'，跳過")
                continue

            # 找到了連續的 "奉獻收據抬頭:"
            fengxian_index = fengxian_idx
            colon_index = fengxian_idx + 3
            logger.info(f"  [找到] 連續的 '奉獻收據抬頭:' 標籤: indices=[{fengxian_idx}, {fengxian_idx+1}, {fengxian_idx+2}, {fengxian_idx+3}]")
            break

        if fengxian_index < 0:
            logger.warning("[階段10] 未找到連續的 '奉獻收據抬頭:' 標籤")
            return

        self.receipt_title_label = "奉獻收據抬頭:"
        logger.info(f"  奉獻 index={fengxian_index}, : index={colon_index}")

        # 方法一：往前找抬頭（從 "奉獻" 的前一個 index 開始）
        self._find_receipt_title_backwards(index_to_block, fengxian_index)

        # 方法二：��果往前沒找到，往後找
        if not self.found_title:
            self._find_receipt_title_forwards(all_blocks, index_to_block, colon_index)

    def _find_receipt_title_backwards(self, index_to_block: Dict[int, Dict], fengxian_index: int):
        """
        往前找奉獻收據抬頭

        從 "奉獻" 的前一個 index 開始往前找，
        直到找到 ":" 或 "日" 才停止，
        然後合併停止點的下一個 index 到 "奉獻" 前一個 index 的所有內容
        """
        if fengxian_index <= 0:
            logger.info("奉獻 index 太小，無法往前找抬頭")
            return

        # 從 "奉獻" 的前一個 index 開始往前找
        current_index = fengxian_index - 1
        stop_index = -1  # 停止位置（":" 或 "日" 的 index）

        while current_index >= 0:
            block = index_to_block.get(current_index)
            if block is None:
                current_index -= 1
                continue

            text = block.get("text", "").strip()

            # 檢查是否為 ":" 或 "日"
            if text in [":", "：", ";", "日"]:
                stop_index = current_index
                logger.debug(f"往前找到停止標記 '{text}' at index={current_index}")
                break

            current_index -= 1

        if stop_index < 0:
            logger.info("往前找沒有找到 ':' 或 '日'")
            return

        # 合併從 stop_index + 1 到 fengxian_index - 1 的所有內容
        title_parts = []
        for idx in range(stop_index + 1, fengxian_index):
            block = index_to_block.get(idx)
            if block:
                text = block.get("text", "").strip()
                if text:
                    title_parts.append(text)

        if title_parts:
            self.receipt_title = "".join(title_parts)
            self.found_title = True
            logger.info(f"往前找到奉獻收據抬頭: {self.receipt_title}")
        else:
            logger.info("往前找沒有找到抬頭內容")

    def _find_receipt_title_forwards(self, all_blocks: List[Dict], index_to_block: Dict[int, Dict], colon_index: int):
        """
        往後找奉獻收據抬頭

        從 ":" 的下一個 index 開始往後找，
        直到找到下一個 "奉獻" 才停止，
        "奉獻" 不加入抬頭中
        """
        if colon_index < 0:
            logger.info("沒有找到 ':'，無法往後找抬頭")
            return

        title_parts = []
        current_index = colon_index + 1

        # 取得最大 index
        max_index = max(b["index"] for b in all_blocks) if all_blocks else 0

        while current_index <= max_index:
            block = index_to_block.get(current_index)
            if block is None:
                current_index += 1
                continue

            text = block.get("text", "").strip()

            # 如果找到 "奉獻"，停止（不加入）
            if text == "奉獻":
                logger.debug(f"往後找到 '奉獻' at index={current_index}，停止")
                break

            # 加入抬頭
            if text:
                title_parts.append(text)
                logger.debug(f"往後找到抬頭部分 '{text}' at index={current_index}")

            current_index += 1

        if title_parts:
            self.receipt_title = "".join(title_parts)
            self.found_title = True
            logger.info(f"往後找到奉獻收據抬頭: {self.receipt_title}")
        else:
            logger.info("往後找沒有找到抬頭內容")

    # ==================== 第十一階段：奉獻收據寄送地址 ====================

    # 奉獻收據寄送地址標籤關鍵字
    MAILING_ADDRESS_KEYWORDS = ["奉獻", "收據", "寄送", "地址", ":"]

    def _stage11_mailing_address(self):
        """第十一階段：處理奉獻收據寄送地址"""
        logger.info("=" * 60)
        logger.info("===== 第十一階段：奉獻收據寄送地址 =====")
        logger.info("=" * 60)

        all_blocks = self.original_blocks

        # 建立 index 到 block 的映射
        index_to_block = {b["index"]: b for b in all_blocks}

        # 尋找奉獻收據寄送���址標籤（必須是連續的關鍵字）
        logger.info("[階段11] 尋找 '奉獻收據寄送地址:' 標籤（連續關鍵字）...")

        fengxian_index = -1  # "奉獻" 的 index
        colon_index = -1  # ":" 的 index

        # 先找所有 "奉獻" 的位置
        fengxian_candidates = []
        for block in all_blocks:
            if block.get("text", "").strip() == "奉獻":
                fengxian_candidates.append(block["index"])

        logger.info(f"  找到 {len(fengxian_candidates)} 個 '奉獻' 候選: {fengxian_candidates}")

        # 對每個 "奉獻" 候選，檢��後面是否有連續的 "收據", "寄送", "地址", ":"
        for fengxian_idx in fengxian_candidates:
            logger.debug(f"  檢查候選 '奉獻' at index={fengxian_idx}...")

            # 檢查 index+1 ���否為 "收據"
            shouju_block = index_to_block.get(fengxian_idx + 1)
            if not shouju_block or shouju_block.get("text", "").strip() != "收據":
                logger.debug(f"    index+1 不是 '收據'，跳過")
                continue

            # 檢查 index+2 是否為 "��送"
            jisong_block = index_to_block.get(fengxian_idx + 2)
            if not jisong_block or jisong_block.get("text", "").strip() != "寄送":
                logger.debug(f"    index+2 不是 '寄送'，跳過")
                continue

            # 檢查 index+3 是否為 "地址"
            dizhi_block = index_to_block.get(fengxian_idx + 3)
            if not dizhi_block or dizhi_block.get("text", "").strip() != "地址":
                logger.debug(f"    index+3 不是 '地址'，跳過")
                continue

            # 檢查 index+4 是��為 ":"
            colon_block = index_to_block.get(fengxian_idx + 4)
            if not colon_block or colon_block.get("text", "").strip() not in [":", "：", ";"]:
                logger.debug(f"    index+4 不是 ':'，跳過")
                continue

            # 找到了連續的 "奉獻收據寄送地址:"
            fengxian_index = fengxian_idx
            colon_index = fengxian_idx + 4
            logger.info(f"  [找���] 連續的 '奉獻收據寄送地址:' 標籤: indices=[{fengxian_idx}, {fengxian_idx+1}, {fengxian_idx+2}, {fengxian_idx+3}, {fengxian_idx+4}]")
            break

        if fengxian_index < 0:
            logger.warning("[階段11] 未找到連續的 '奉獻收據寄送地址:' 標籤")
            return

        self.mailing_address_label = "奉獻收據寄送地址:"
        logger.info(f"  奉獻 index={fengxian_index}, : index={colon_index}")

        # 從 ":" 的下一個 index 開始往後找地址
        self._find_mailing_address(all_blocks, index_to_block, colon_index)

    def _find_mailing_address(self, all_blocks: List[Dict], index_to_block: Dict[int, Dict], colon_index: int):
        """
        尋找奉獻收據寄送地址

        從 ":" 的下一個 index 開始往後找，
        直到找到 "聯絡" 才停止，
        "聯絡" 不加入地址中
        如果開頭是 "000000" 則忽略
        """
        if colon_index < 0:
            logger.info("沒有找到 ':'，無法尋找地址")
            return

        address_parts = []
        current_index = colon_index + 1

        # 取得最大 index
        max_index = max(b["index"] for b in all_blocks) if all_blocks else 0

        while current_index <= max_index:
            block = index_to_block.get(current_index)
            if block is None:
                current_index += 1
                continue

            text = block.get("text", "").strip()

            # 如果找到 "聯絡"，停止（不加入）
            if text == "聯絡":
                logger.debug(f"找到 '聯絡' at index={current_index}，停止")
                break

            # 加入地址
            if text:
                # 檢查是否為 6 位數字（郵遞區號）
                if len(text) == 6 and text.isdigit():
                    # 如果是 "000000" 則忽略
                    if text == "000000":
                        logger.debug(f"忽略無效郵遞區號 '000000' at index={current_index}")
                        current_index += 1
                        continue
                    # 其他 6 位數字視為郵遞區號，加入
                    logger.debug(f"找到郵遞區號 '{text}' at index={current_index}")

                address_parts.append(text)
                logger.debug(f"找到地址部分 '{text}' at index={current_index}")

            current_index += 1

        if address_parts:
            self.mailing_address = "".join(address_parts)
            logger.info(f"找到奉獻收據寄送地址: {self.mailing_address}")
        else:
            logger.info("未找到奉獻收據寄送地址內容")

    # ==================== 第十二階段：聯絡電話 ====================

    # 聯絡電話標籤關鍵字
    TELEPHONE_KEYWORDS = ["聯絡", "電話", ":"]

    def _stage12_telephone(self):
        """第十二階段：處理聯絡電話"""
        logger.info("===== 第十二階段：聯絡電話 =====")

        all_blocks = self.original_blocks

        # 建立 index 到 block 的映射
        index_to_block = {b["index"]: b for b in all_blocks}

        # 尋找聯絡電話標籤
        keyword_index = 0
        colon_index = -1  # ":" 的 index

        for block in all_blocks:
            text = block.get("text", "").strip()

            if keyword_index < len(self.TELEPHONE_KEYWORDS):
                expected = self.TELEPHONE_KEYWORDS[keyword_index]

                # 允許冒號的多種形式
                text_matches = (
                    text == expected or
                    (expected == ":" and text in [":", "：", ";"])
                )

                if text_matches:
                    if expected == ":":
                        colon_index = block["index"]
                    keyword_index += 1
                    logger.debug(f"找到聯絡電話關鍵字 '{expected}' (實際: '{text}') at index={block['index']}")

        if keyword_index < len(self.TELEPHONE_KEYWORDS):
            logger.info(f"未找到完整的聯絡電話標籤 (找到 {keyword_index}/{len(self.TELEPHONE_KEYWORDS)} 個關鍵字)")
            return

        self.telephone_label = "聯絡電話:"
        logger.info(f"找到聯絡電話標籤，: index={colon_index}")

        # 從 ":" 的下一個 index 開始往後找電話號碼
        self._find_telephone_number(all_blocks, index_to_block, colon_index)

    def _is_phone_number(self, text: str) -> bool:
        """
        檢查是否為有效的電話號碼格式

        電話號碼只能由數字和 '-' 組成
        """
        if not text:
            return False
        # 移除所有的 '-' 後檢查是否都是數字
        clean_text = text.replace("-", "")
        return clean_text.isdigit() and len(clean_text) > 0

    def _find_telephone_number(self, all_blocks: List[Dict], index_to_block: Dict[int, Dict], colon_index: int):
        """
        尋找聯絡電話號碼

        從 ":" 的下一個 index 開始往後找，
        直到找到最後一筆或非電話號碼格式
        電話號碼只能由數字和 '-' 組成
        """
        if colon_index < 0:
            logger.info("沒有找到 ':'，無法尋找電話號碼")
            return

        current_index = colon_index + 1

        # 取得最大 index
        max_index = max(b["index"] for b in all_blocks) if all_blocks else 0

        while current_index <= max_index:
            block = index_to_block.get(current_index)
            if block is None:
                current_index += 1
                continue

            text = block.get("text", "").strip()

            # 檢查是否為電話號碼格式（數字和 '-' 組成）
            if self._is_phone_number(text):
                self.telephone_number = text
                logger.info(f"找到電話號碼 '{text}' at index={current_index}")
                break

            current_index += 1

        if self.telephone_number:
            logger.info(f"找到聯絡電話: {self.telephone_number}")
        else:
            logger.info("未找到聯絡電話號碼")

    # ==================== 第十三階段：電子信箱 ====================

    # 電子信箱標籤關鍵字
    EMAIL_KEYWORDS = ["電子", "信箱", ":"]

    def _stage13_email(self):
        """第十三階段：處理電子信箱"""
        logger.info("===== 第十三階段：電子信箱 =====")

        all_blocks = self.original_blocks

        # 建立 index 到 block 的映射
        index_to_block = {b["index"]: b for b in all_blocks}

        # 尋找電子信箱標籤
        keyword_index = 0
        colon_index = -1  # ":" 的 index

        for block in all_blocks:
            text = block.get("text", "").strip()

            if keyword_index < len(self.EMAIL_KEYWORDS):
                expected = self.EMAIL_KEYWORDS[keyword_index]

                # 允許冒號的多種形式
                text_matches = (
                    text == expected or
                    (expected == ":" and text in [":", "：", ";"])
                )

                if text_matches:
                    if expected == ":":
                        colon_index = block["index"]
                    keyword_index += 1
                    logger.debug(f"找到電子信箱關鍵字 '{expected}' (實際: '{text}') at index={block['index']}")

        if keyword_index < len(self.EMAIL_KEYWORDS):
            logger.info(f"未找到完整的電子信箱標籤 (找到 {keyword_index}/{len(self.EMAIL_KEYWORDS)} 個關鍵字)")
            return

        self.mail_title = "電子信箱:"
        logger.info(f"找到電子信箱標籤，: index={colon_index}")

        # 從 ":" 的下一個 index 開始往後找電子郵箱
        self._find_email_address(all_blocks, index_to_block, colon_index)

    def _is_valid_email(self, text: str) -> bool:
        """
        檢查是否為有效的電子郵箱格式

        Email 內容一定都是由 ASCII 可見字元組成，而且一定有一個 '@' 在其中
        """
        if not text:
            return False

        # 必須包含 '@' 符號
        if "@" not in text:
            return False

        # 檢查是否都是 ASCII 可見字元（字元碼 33-126）
        for char in text:
            code = ord(char)
            if code < 33 or code > 126:
                return False

        # 確保 '@' 不在開頭或結尾
        if text.startswith("@") or text.endswith("@"):
            return False

        return True

    def _find_email_address(self, all_blocks: List[Dict], index_to_block: Dict[int, Dict], colon_index: int):
        """
        尋找電子郵箱地址

        從 ":" 的下一個 index 開始往後找，
        直到找到最後一筆才停止，
        Email 內容必須是 ASCII 可見字元且包含 '@'
        """
        if colon_index < 0:
            logger.info("沒有找到 ':'，無法尋找電子郵箱")
            return

        current_index = colon_index + 1

        # 取得最大 index
        max_index = max(b["index"] for b in all_blocks) if all_blocks else 0

        while current_index <= max_index:
            block = index_to_block.get(current_index)
            if block is None:
                current_index += 1
                continue

            text = block.get("text", "").strip()

            # 檢查是否為有效的電子郵箱格式
            if self._is_valid_email(text):
                self.mail = text
                logger.info(f"找到電子郵箱 '{text}' at index={current_index}")
                # 繼續往後找，取最後一個符合條件的
                # 如果後面還有更好的匹配，就更新

            current_index += 1

        if self.mail:
            logger.info(f"找到電子信箱: {self.mail}")
        else:
            logger.info("未找到電子郵箱地址")

    # ==================== 產生輸出 ====================

    def _generate_output(self) -> Dict[str, Any]:
        """產生最終輸出"""
        logger.info("=" * 60)
        logger.info("===== 產生最終輸出 =====")
        logger.info("=" * 60)

        output_lines = []

        # 輸出捐獻項目和金額
        logger.info("[輸出檢查] 捐獻項目和金額:")
        if hasattr(self, "matched_item_name") and self.matched_item_name and self.donate_money:
            line = f"{self.matched_item_name}：{self.donate_money.amount}"
            output_lines.append(line)
            logger.info(f"  [輸出] {line}")
        else:
            logger.warning(f"  [未輸出] matched_item_name={getattr(self, 'matched_item_name', 'N/A')}, donate_money={self.donate_money}")
            if not self.donate_money:
                logger.warning("    原因：未找到捐獻金額")
            if not getattr(self, "matched_item_name", ""):
                logger.warning("    原因：未配對到任何捐獻項目")

        # 輸出合計
        logger.info("[輸出檢查] 合計金額:")
        if self.total_amount:
            line = f"合計：{self.total_amount}"
            output_lines.append(line)
            logger.info(f"  [輸出] {line}")
        else:
            logger.warning("  [未輸出] total_amount 為空")
            logger.warning("    原因：未找到合計金額")

        # 第六階段：輸出有勾選的收據選項（三者互斥，只輸出一個）
        logger.info("[輸出檢查] 收據選項（互斥，只輸出一個）:")
        logger.info(f"  IRS (代上傳國稅局): found={bool(self.receipt_irs)}, checked={self.receipt_irs_checked}")
        logger.info(f"  電子收據: found={bool(self.receipt_electronic)}, checked={self.receipt_electronic_checked}")
        logger.info(f"  紙本收據: found={bool(self.receipt_paper)}, checked={self.receipt_paper_checked}")

        receipt_output = None  # 只輸出一個收據選項

        # 優先順序：紙本收據 > 電子收據 > IRS（按常見使用頻率）
        if self.receipt_paper_checked and self.receipt_paper:
            receipt_output = self.receipt_paper.get("text", "年度紙本收據")
            logger.info(f"  [選中] 紙本收據")
        elif self.receipt_electronic_checked and self.receipt_electronic:
            receipt_output = self.receipt_electronic.get("text", "電子收據")
            logger.info(f"  [選中] 電子收據")
        elif self.receipt_irs_checked and self.receipt_irs:
            receipt_output = self.receipt_irs.get("text", "代上傳國稅局無紙本")
            logger.info(f"  [選中] IRS")

        if receipt_output:
            output_lines.append(receipt_output)
            logger.info(f"  [輸出] {receipt_output}")
        else:
            # 如果三種收據選項都沒有勾選，輸出「不需要奉獻收據」
            if self.no_receipt_str:
                output_lines.append(self.no_receipt_str)
                logger.info(f"  [輸出] {self.no_receipt_str}（三種收據選項都沒有勾選）")
            else:
                logger.info("  [未輸出] 原因：沒有任何收據選項被勾選，也沒有找到「不需要奉獻收據」")

        # 輸出不同意揭露聲明（如果有勾選）
        logger.info("[輸出檢查] 不同意揭露聲明:")
        logger.info(f"  聲明文字: '{self.agree_public_str[:20]}...' if len > 20 else '{self.agree_public_str}'")
        logger.info(f"  是否勾選: {self.agree_public_checked}")
        if self.agree_public_checked and self.agree_public_str:
            output_lines.append(self.agree_public_str)
            logger.info(f"  [輸出] {self.agree_public_str}")
        elif not self.agree_public_str:
            logger.info("    [未輸出] 原因：未找到聲明文字")
        elif not self.agree_public_checked:
            logger.info("    [未輸出] 原因：聲明未勾選")

        # 第七階段：輸出身分證字號
        logger.info("[輸出檢查] 身分證字號:")
        logger.info(f"  標籤: '{self.id_card_label}', 號碼: '{self.id_card_number}'")
        if self.id_card_label and self.id_card_number:
            line = f"{self.id_card_label}{self.id_card_number}"
            output_lines.append(line)
            logger.info(f"  [輸出] {line}")
        else:
            if not self.id_card_label:
                logger.info("    [未輸出] 原因：未找到身分證字號標籤")
            if not self.id_card_number:
                logger.info("    [未輸出] 原因：未找到有效的身分證字號")

        # 第八階段：輸出奉獻者姓名
        logger.info("[輸出檢查] 奉獻者姓名:")
        logger.info(f"  標籤: '{self.donor_name_label}', 姓名: '{self.donor_name}', found: {self.found_name}")
        if self.donor_name_label and self.donor_name:
            line = f"{self.donor_name_label}{self.donor_name}"
            output_lines.append(line)
            logger.info(f"  [輸出] {line}")
        else:
            if not self.donor_name_label:
                logger.info("    [未輸出] 原因：未找到奉獻者姓名標籤")
            if not self.donor_name:
                logger.info("    [未輸出] 原因：未找到奉獻者姓名內容（往前往後都沒找到）")

        # 第九階段：輸出奉獻日期
        logger.info("[輸出檢查] 奉獻日期:")
        logger.info(f"  標籤: '{self.donation_date_label}', 日期: '{self.donation_date}'")
        if self.donation_date_label and self.donation_date:
            line = f"{self.donation_date_label}{self.donation_date}"
            output_lines.append(line)
            logger.info(f"  [輸出] {line}")
        else:
            if not self.donation_date_label:
                logger.info("    [未輸出] 原因：未找到奉獻日期標籤")
            if not self.donation_date:
                logger.info("    [未輸出] 原因：未找到奉獻日期內容")

        # 第十階段：輸出奉獻收據抬頭
        logger.info("[輸出檢查] 奉獻收據抬頭:")
        logger.info(f"  標籤: '{self.receipt_title_label}', 抬頭: '{self.receipt_title}', found: {self.found_title}")
        if self.receipt_title_label and self.receipt_title:
            line = f"{self.receipt_title_label}{self.receipt_title}"
            output_lines.append(line)
            logger.info(f"  [輸出] {line}")
        else:
            if not self.receipt_title_label:
                logger.info("    [未輸出] 原因：未找到奉獻收據抬頭標籤")
            if not self.receipt_title:
                logger.info("    [未輸出] 原因：未找到奉獻收據抬頭內容（往前往後都沒找到）")

        # 第十一階段：輸出奉獻收據寄送地址
        logger.info("[輸出檢查] 奉獻收據寄送地址:")
        logger.info(f"  標籤: '{self.mailing_address_label}', 地址: '{self.mailing_address}'")
        if self.mailing_address_label and self.mailing_address:
            line = f"{self.mailing_address_label}{self.mailing_address}"
            output_lines.append(line)
            logger.info(f"  [輸出] {line}")
        else:
            if not self.mailing_address_label:
                logger.info("    [未輸出] 原因：未找到奉獻收據寄送地址標籤")
            if not self.mailing_address:
                logger.info("    [未輸出] 原因：未找到奉獻收據寄送地址內容")

        # 第十二階段：輸出聯絡電話（長度 > 8 才輸出）
        logger.info("[輸出檢查] 聯絡電話:")
        logger.info(f"  標籤: '{self.telephone_label}', 電話: '{self.telephone_number}', 長度: {len(self.telephone_number)}")
        if self.telephone_label and self.telephone_number and len(self.telephone_number) > 8:
            line = f"{self.telephone_label}{self.telephone_number}"
            output_lines.append(line)
            logger.info(f"  [輸出] {line}")
        else:
            if not self.telephone_label:
                logger.info("    [未輸出] 原因：未找到聯絡電話標籤")
            if not self.telephone_number:
                logger.info("    [未輸出] 原因：未找到聯絡電話號碼")
            elif len(self.telephone_number) <= 8:
                logger.info(f"    [未輸出] 原因：電話號碼長度 {len(self.telephone_number)} <= 8")

        # 第十三階段：輸出電子信箱
        logger.info("[輸出檢查] 電子信箱:")
        logger.info(f"  標籤: '{self.mail_title}', 信箱: '{self.mail}'")
        if self.mail_title and self.mail:
            line = f"{self.mail_title}{self.mail}"
            output_lines.append(line)
            logger.info(f"  [輸出] {line}")
        else:
            if not self.mail_title:
                logger.info("    [未輸出] 原因：未找到電子信箱標籤")
            if not self.mail:
                logger.info("    [未輸出] 原因：未找到電子信箱地址（沒有 @ 符號的 ASCII 文字）")

        output_text = "\n".join(output_lines)

        # 建立結構化的 Donate No 資料
        donate_no = {
            "envelope_top_y": self.envelope_top_y,
            "items": [],
        }

        for i, item in enumerate(self.donate_items, 1):
            donate_no["items"].append({
                f"Donate Item-{i}": {
                    "name": item.name,
                    "keywords": item.keywords,
                    "indices": item.indices,
                    "y_values": item.y_values,
                    "avg_y": item.avg_y,
                }
            })

        if self.donate_money:
            donate_no["Donate_Money"] = {
                "amount": self.donate_money.amount,
                "index": self.donate_money.index,
                "y": self.donate_money.y,
                "matched_item": getattr(self, "matched_item_name", ""),
            }

        donate_no["total"] = self.total_amount
        donate_no["heji_y"] = self.heji_y
        donate_no["heji_index"] = self.heji_index

        # 第五階段：不同意揭露聲明資料
        if self.agree_public_str:
            donate_no["Agree_Public_Str"] = {
                "text": self.agree_public_str,
                "avg_y": self.agree_public_avg_y,
            }

        if self.no_receipt_str:
            donate_no["No_Receipt"] = {
                "text": self.no_receipt_str,
                "avg_y": self.no_receipt_avg_y,
            }

        # 第六階段：收據選項資料
        receipt_data = {}
        if self.receipt_irs:
            receipt_data["IRS"] = {
                **self.receipt_irs,
                "checked": self.receipt_irs_checked,
            }
        if self.receipt_electronic:
            receipt_data["Electronic_Receipt"] = {
                **self.receipt_electronic,
                "checked": self.receipt_electronic_checked,
            }
        if self.receipt_paper:
            receipt_data["Paper_Receipt"] = {
                **self.receipt_paper,
                "checked": self.receipt_paper_checked,
            }
        if receipt_data:
            donate_no["Receipt"] = receipt_data

        # 更新 Agree_Public_Str 的勾選狀態
        if "Agree_Public_Str" in donate_no:
            donate_no["Agree_Public_Str"]["checked"] = self.agree_public_checked

        # 第七階段：身分證字號資料
        if self.id_card_label:
            donate_no["ID_Card"] = {
                "label": self.id_card_label,
                "number": self.id_card_number if self.id_card_number else None,
            }

        # 第八階段：奉獻者姓名資料
        if self.donor_name_label:
            donate_no["Donor_Name"] = {
                "label": self.donor_name_label,
                "name": self.donor_name if self.donor_name else None,
                "found": self.found_name,
            }

        # 第九階段：奉獻日期資料
        if self.donation_date_label:
            donate_no["Donation_Date"] = {
                "label": self.donation_date_label,
                "date": self.donation_date if self.donation_date else None,
            }

        # 第十階段：奉獻收據抬頭資料
        if self.receipt_title_label:
            donate_no["Receipt_Title"] = {
                "label": self.receipt_title_label,
                "title": self.receipt_title if self.receipt_title else None,
                "found": self.found_title,
            }

        # 第十一階段：奉獻收據寄送地址資料
        if self.mailing_address_label:
            donate_no["Mailing_Address"] = {
                "label": self.mailing_address_label,
                "address": self.mailing_address if self.mailing_address else None,
            }

        # 第十二階段：聯絡電話資料
        if self.telephone_label:
            donate_no["Telephone"] = {
                "label": self.telephone_label,
                "number": self.telephone_number if self.telephone_number else None,
            }

        # 第十三階段：電子信箱資料
        if self.mail_title:
            donate_no["Email"] = {
                "mail_title": self.mail_title,
                "mail": self.mail if self.mail else None,
            }

        result = {
            "output_text": output_text,
            "donate_no": donate_no,
            "filtered_blocks": self.filtered_blocks,
            "stats": {
                "original_blocks_count": len(self.original_blocks),
                "filtered_blocks_count": len(self.filtered_blocks),
                "filtered_count": len(self.original_blocks) - len(self.filtered_blocks),
                "announcement_filtered_count": len(self.announcement_filtered_indices),
            },
        }

        logger.info(f"處理完成: {result['stats']}")
        return result


def process_donation_ocr(blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    處理捐獻袋 OCR 結果的便捷函數

    Args:
        blocks: Vision.json 中的 blocks 陣列

    Returns:
        處理結果
    """
    processor = DonationRulesProcessor(blocks)
    return processor.process()


def process_vision_json_file(vision_json_path: str) -> Dict[str, Any]:
    """
    處理 Vision.json 檔案

    Args:
        vision_json_path: Vision.json 檔案路徑

    Returns:
        處理結果
    """
    path = Path(vision_json_path)
    if not path.exists():
        raise FileNotFoundError(f"Vision.json 不存在: {vision_json_path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    blocks = data.get("blocks", [])
    return process_donation_ocr(blocks)
