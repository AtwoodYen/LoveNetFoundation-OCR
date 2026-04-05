import SwiftUI

/// 引導框狀態
enum GuideFrameState {
    case searching   // 尋找信封中（紅框）
    case adjusting   // 需要調整（黃框）
    case ready       // 準備好拍照（綠框）

    var color: Color {
        switch self {
        case .searching: return .red
        case .adjusting: return .yellow
        case .ready: return .green
        }
    }

    var message: String {
        switch self {
        case .searching: return "請將信封對齊引導框"
        case .adjusting: return "請調整位置或角度"
        case .ready: return "位置正確，請拍照"
        }
    }
}

/// 引導框覆蓋層視圖：顯示直式信封引導框與半透明遮罩
struct GuideOverlayView: View {
    /// 引導框狀態
    var state: GuideFrameState = .searching

    /// 信封長寬比（寬:高）- 直式信封約為 1:2
    private let envelopeAspectRatio: CGFloat = 0.5 // width / height

    /// 引導框佔螢幕高度的比例
    private let guideHeightRatio: CGFloat = 0.7

    /// 角落標記長度
    private let cornerLength: CGFloat = 30

    /// 角落標記線寬
    private let cornerLineWidth: CGFloat = 4

    var body: some View {
        GeometryReader { geometry in
            let guideRect = calculateGuideRect(in: geometry.size)

            ZStack {
                // 半透明遮罩（引導框外部區域）
                MaskOverlay(guideRect: guideRect)
                    .fill(Color.black.opacity(0.5))

                // 引導框邊框
                GuideFrameBorder(rect: guideRect, state: state)

                // 角落標記
                CornerMarkers(
                    rect: guideRect,
                    color: state.color,
                    length: cornerLength,
                    lineWidth: cornerLineWidth
                )
            }
        }
        .ignoresSafeArea()
    }

    /// 計算引導框位置
    private func calculateGuideRect(in size: CGSize) -> CGRect {
        let guideHeight = size.height * guideHeightRatio
        let guideWidth = guideHeight * envelopeAspectRatio

        // 確保寬度不超過螢幕（留邊距）
        let maxWidth = size.width - 40
        let finalWidth = min(guideWidth, maxWidth)
        let finalHeight = finalWidth / envelopeAspectRatio

        let x = (size.width - finalWidth) / 2
        let y = (size.height - finalHeight) / 2 - 30 - size.height * 0.03

        return CGRect(x: x, y: y, width: finalWidth, height: finalHeight)
    }
}

// MARK: - Mask Overlay

/// 遮罩形狀：整個畫面減去引導框區域
struct MaskOverlay: Shape {
    let guideRect: CGRect

    func path(in rect: CGRect) -> Path {
        var path = Path()

        // 整個畫面
        path.addRect(rect)

        // 減去引導框區域（圓角矩形）
        let guidePath = Path(roundedRect: guideRect, cornerRadius: 8)
        path = path.subtracting(guidePath)

        return path
    }
}

// MARK: - Guide Frame Border

/// 引導框邊框
struct GuideFrameBorder: View {
    let rect: CGRect
    let state: GuideFrameState

    var body: some View {
        RoundedRectangle(cornerRadius: 8)
            .stroke(state.color, lineWidth: 2)
            .frame(width: rect.width, height: rect.height)
            .position(x: rect.midX, y: rect.midY)
            .animation(.easeInOut(duration: 0.3), value: state)
    }
}

// MARK: - Corner Markers

/// 角落標記：四個 L 形標記
struct CornerMarkers: View {
    let rect: CGRect
    let color: Color
    let length: CGFloat
    let lineWidth: CGFloat

    var body: some View {
        Canvas { context, size in
            let corners: [(CGPoint, CGFloat)] = [
                (CGPoint(x: rect.minX, y: rect.minY), 0),           // 左上
                (CGPoint(x: rect.maxX, y: rect.minY), .pi / 2),    // 右上
                (CGPoint(x: rect.maxX, y: rect.maxY), .pi),        // 右下
                (CGPoint(x: rect.minX, y: rect.maxY), -.pi / 2)    // 左下
            ]

            for (point, rotation) in corners {
                drawCornerMarker(
                    context: context,
                    at: point,
                    rotation: rotation,
                    color: color
                )
            }
        }
    }

    private func drawCornerMarker(
        context: GraphicsContext,
        at point: CGPoint,
        rotation: CGFloat,
        color: Color
    ) {
        var path = Path()

        // L 形路徑（從角落向外延伸）
        path.move(to: CGPoint(x: 0, y: length))
        path.addLine(to: CGPoint(x: 0, y: 0))
        path.addLine(to: CGPoint(x: length, y: 0))

        var transform = CGAffineTransform.identity
        transform = transform.translatedBy(x: point.x, y: point.y)
        transform = transform.rotated(by: rotation)

        let transformedPath = path.applying(transform)

        context.stroke(
            transformedPath,
            with: .color(color),
            style: StrokeStyle(lineWidth: lineWidth, lineCap: .round, lineJoin: .round)
        )
    }
}

// MARK: - Preview

#Preview("Searching") {
    ZStack {
        Color.gray
        GuideOverlayView(state: .searching)
    }
}

#Preview("Ready") {
    ZStack {
        Color.gray
        GuideOverlayView(state: .ready)
    }
}
