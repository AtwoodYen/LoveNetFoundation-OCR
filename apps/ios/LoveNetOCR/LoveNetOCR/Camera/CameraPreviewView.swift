import AVFoundation
import SwiftUI
import UIKit

/// 相機預覽視圖：包裝 AVCaptureVideoPreviewLayer 供 SwiftUI 使用
struct CameraPreviewView: UIViewRepresentable {
    let session: AVCaptureSession

    /// 點擊對焦的回調
    var onTapToFocus: ((CGPoint) -> Void)?

    func makeUIView(context: Context) -> CameraPreviewUIView {
        let view = CameraPreviewUIView()
        view.session = session
        view.onTapToFocus = onTapToFocus
        return view
    }

    func updateUIView(_ uiView: CameraPreviewUIView, context: Context) {
        uiView.session = session
        uiView.onTapToFocus = onTapToFocus
    }
}

/// 底層 UIView：管理 AVCaptureVideoPreviewLayer
final class CameraPreviewUIView: UIView {

    var session: AVCaptureSession? {
        didSet {
            previewLayer.session = session
        }
    }

    var onTapToFocus: ((CGPoint) -> Void)?

    private var previewLayer: AVCaptureVideoPreviewLayer {
        layer as! AVCaptureVideoPreviewLayer
    }

    override class var layerClass: AnyClass {
        AVCaptureVideoPreviewLayer.self
    }

    override init(frame: CGRect) {
        super.init(frame: frame)
        setupView()
    }

    required init?(coder: NSCoder) {
        super.init(coder: coder)
        setupView()
    }

    private func setupView() {
        backgroundColor = .black

        // 設定預覽層填滿並保持比例
        previewLayer.videoGravity = .resizeAspectFill

        // 添加點擊手勢（對焦用）
        let tapGesture = UITapGestureRecognizer(target: self, action: #selector(handleTap(_:)))
        addGestureRecognizer(tapGesture)
    }

    override func layoutSubviews() {
        super.layoutSubviews()
        previewLayer.frame = bounds

        // 更新預覽方向
        updatePreviewOrientation()
    }

    private func updatePreviewOrientation() {
        guard let connection = previewLayer.connection else { return }

        // 根據裝置方向設定預覽方向
        if connection.isVideoRotationAngleSupported(90) {
            // iOS 17+ 使用新 API
            let angle: CGFloat
            switch UIDevice.current.orientation {
            case .portrait:
                angle = 90
            case .portraitUpsideDown:
                angle = 270
            case .landscapeLeft:
                angle = 0
            case .landscapeRight:
                angle = 180
            default:
                angle = 90 // 預設直向
            }
            connection.videoRotationAngle = angle
        }
    }

    @objc private func handleTap(_ gesture: UITapGestureRecognizer) {
        let location = gesture.location(in: self)

        // 轉換為相機座標系統（0-1 範圍）
        let devicePoint = previewLayer.captureDevicePointConverted(fromLayerPoint: location)

        // 顯示對焦動畫
        showFocusAnimation(at: location)

        // 回調對焦點
        onTapToFocus?(devicePoint)
    }

    /// 顯示對焦框動畫
    private func showFocusAnimation(at point: CGPoint) {
        // 移除既有的對焦框
        layer.sublayers?.filter { $0.name == "focusBox" }.forEach { $0.removeFromSuperlayer() }

        // 建立對焦框
        let focusBox = CAShapeLayer()
        focusBox.name = "focusBox"

        let boxSize: CGFloat = 80
        let boxRect = CGRect(
            x: point.x - boxSize / 2,
            y: point.y - boxSize / 2,
            width: boxSize,
            height: boxSize
        )

        focusBox.path = UIBezierPath(roundedRect: boxRect, cornerRadius: 4).cgPath
        focusBox.strokeColor = UIColor.yellow.cgColor
        focusBox.fillColor = UIColor.clear.cgColor
        focusBox.lineWidth = 2

        layer.addSublayer(focusBox)

        // 動畫：縮放 + 淡出
        let scaleAnimation = CABasicAnimation(keyPath: "transform.scale")
        scaleAnimation.fromValue = 1.2
        scaleAnimation.toValue = 1.0
        scaleAnimation.duration = 0.2

        let opacityAnimation = CABasicAnimation(keyPath: "opacity")
        opacityAnimation.fromValue = 1.0
        opacityAnimation.toValue = 0.0
        opacityAnimation.duration = 0.8
        opacityAnimation.beginTime = CACurrentMediaTime() + 0.5

        focusBox.add(scaleAnimation, forKey: "scale")
        focusBox.add(opacityAnimation, forKey: "opacity")

        // 延遲移除
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.3) {
            focusBox.removeFromSuperlayer()
        }
    }
}
