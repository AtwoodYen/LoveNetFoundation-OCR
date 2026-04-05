import SwiftUI

/// 檢測狀態面板：清楚顯示 5 項檢測結果
struct DetectionStatusView: View {
    let result: EnvelopeDetectionResult
    let isAdjustingFocus: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            // 1. 對焦檢測
            FocusStatusRow(
                isAdjustingFocus: isAdjustingFocus,
                sharpnessScore: result.sharpnessScore
            )

            // 2. 位置檢測
            PositionStatusRow(
                isDetected: result.isDetected,
                positionStatus: result.positionStatus
            )

            // 3. 傾斜角度檢測
            TiltStatusRow(
                isDetected: result.isDetected,
                tiltAngle: result.tiltAngle,
                tiltDirection: result.tiltDirection
            )

            // 4. 光線檢測
            LightStatusRow(brightnessScore: result.brightnessScore)

            // 5. 距離檢測
            DistanceStatusRow(
                isDetected: result.isDetected,
                coverageRatio: result.coverageRatio,
                distanceStatus: result.distanceStatus
            )
        }
        .padding(.horizontal, 2)
    }
}

// MARK: - 1. 對焦狀態行

struct FocusStatusRow: View {
    let isAdjustingFocus: Bool
    let sharpnessScore: Double

    private var status: (text: String, color: Color) {
        if isAdjustingFocus {
            return ("對焦中...", .yellow)
        } else if sharpnessScore >= 8 {
            return ("清晰 ✓", .green)
        } else {
            return ("模糊 - 請穩定手機", .black)
        }
    }

    var body: some View {
        HStack {
            Text("對焦：")
                .foregroundColor(.white.opacity(0.7))
            Text(status.text)
                .foregroundColor(status.color)
                .fontWeight(.medium)
            Spacer()
            Text("(\(Int(sharpnessScore)))")
                .foregroundColor(.white.opacity(0.5))
                .font(.caption)
        }
        .font(.subheadline)
    }
}

// MARK: - 2. 位置狀態行

struct PositionStatusRow: View {
    let isDetected: Bool
    let positionStatus: PositionStatus

    var body: some View {
        HStack {
            Text("位置：")
                .foregroundColor(.white.opacity(0.7))

            if !isDetected {
                Text("未偵測到信封")
                    .foregroundColor(.black)
            } else {
                Text(positionStatus.message)
                    .foregroundColor(positionStatus.isAligned ? .green : .yellow)
            }
            Spacer()

            // 方向箭頭提示
            if isDetected && !positionStatus.isAligned {
                positionArrows
            }
        }
        .font(.subheadline)
    }

    @ViewBuilder
    private var positionArrows: some View {
        HStack(spacing: 2) {
            if positionStatus.needsMoveLeft {
                Image(systemName: "arrow.left")
            }
            if positionStatus.needsMoveRight {
                Image(systemName: "arrow.right")
            }
            if positionStatus.needsMoveUp {
                Image(systemName: "arrow.up")
            }
            if positionStatus.needsMoveDown {
                Image(systemName: "arrow.down")
            }
        }
        .foregroundColor(.yellow)
        .font(.caption)
    }
}

// MARK: - 3. 傾斜角度狀態行

struct TiltStatusRow: View {
    let isDetected: Bool
    let tiltAngle: Double
    let tiltDirection: TiltDirection

    private var status: (text: String, color: Color) {
        if !isDetected {
            return ("--", .gray)
        } else if tiltAngle < 6 {
            return ("水平 ✓", .green)
        } else if tiltAngle < 20 {
            return (tiltDirection.hint, .yellow)
        } else {
            return (tiltDirection.hint, .black)
        }
    }

    var body: some View {
        HStack {
            Text("傾斜：")
                .foregroundColor(.white.opacity(0.7))
            Text(status.text)
                .foregroundColor(status.color)
                .fontWeight(.medium)
            Spacer()
            if isDetected {
                Text("\(String(format: "%.1f", tiltAngle))°")
                    .foregroundColor(.white.opacity(0.5))
                    .font(.caption)
            }
        }
        .font(.subheadline)
    }
}

// MARK: - 4. 光線狀態行

struct LightStatusRow: View {
    let brightnessScore: Double

    private var status: (text: String, color: Color) {
        if brightnessScore < 0.1 {
            return ("太暗 - 請找光源", .black)
        } else if brightnessScore > 0.95 {
            return ("太亮 - 請避開強光", .black)
        } else {
            return ("正常 ✓", .green)
        }
    }

    var body: some View {
        HStack {
            Text("光線：")
                .foregroundColor(.white.opacity(0.7))
            Text(status.text)
                .foregroundColor(status.color)
                .fontWeight(.medium)
            Spacer()
            Text("\(Int(brightnessScore * 100))%")
                .foregroundColor(.white.opacity(0.5))
                .font(.caption)
        }
        .font(.subheadline)
    }
}

// MARK: - 5. 距離狀態行

struct DistanceStatusRow: View {
    let isDetected: Bool
    let coverageRatio: Double
    let distanceStatus: DistanceStatus

    private var status: (text: String, color: Color) {
        if !isDetected {
            return ("--", .gray)
        }
        switch distanceStatus {
        case .tooFar:
            return ("太遠 - 請靠近", .yellow)
        case .tooClose:
            return ("太近 - 請遠離", .yellow)
        case .good:
            return ("適中 ✓", .green)
        }
    }

    var body: some View {
        HStack {
            Text("距離：")
                .foregroundColor(.white.opacity(0.7))
            Text(status.text)
                .foregroundColor(status.color)
                .fontWeight(.medium)
            Spacer()
            if isDetected {
                Text("\(Int(coverageRatio * 100))%")
                    .foregroundColor(.white.opacity(0.5))
                    .font(.caption)
            }
        }
        .font(.subheadline)
    }
}

// MARK: - 十字輔助線覆蓋層

struct CrosshairOverlayView: View {
    let guideRect: CGRect
    let tiltAngle: Double
    let tiltDirection: TiltDirection
    let showCrosshair: Bool

    var body: some View {
        if showCrosshair {
            Canvas { context, size in
                let centerX = guideRect.midX
                let centerY = guideRect.midY

                // 水平線
                var hPath = Path()
                hPath.move(to: CGPoint(x: guideRect.minX + 20, y: centerY))
                hPath.addLine(to: CGPoint(x: guideRect.maxX - 20, y: centerY))

                context.stroke(
                    hPath,
                    with: .color(.cyan.opacity(0.8)),
                    style: StrokeStyle(lineWidth: 2, dash: [8, 4])
                )

                // 垂直線
                var vPath = Path()
                vPath.move(to: CGPoint(x: centerX, y: guideRect.minY + 20))
                vPath.addLine(to: CGPoint(x: centerX, y: guideRect.maxY - 20))

                context.stroke(
                    vPath,
                    with: .color(.cyan.opacity(0.8)),
                    style: StrokeStyle(lineWidth: 2, dash: [8, 4])
                )

                // 中心點
                let centerCircle = Path(ellipseIn: CGRect(
                    x: centerX - 6,
                    y: centerY - 6,
                    width: 12,
                    height: 12
                ))
                context.stroke(centerCircle, with: .color(.cyan), lineWidth: 2)
            }

            // 旋轉提示箭頭
            VStack {
                Spacer()
                HStack {
                    if tiltDirection == .rotateLeft {
                        RotationHintView(direction: .left)
                        Spacer()
                    } else if tiltDirection == .rotateRight {
                        Spacer()
                        RotationHintView(direction: .right)
                    }
                }
                .padding(.horizontal, 40)
                .padding(.bottom, guideRect.maxY + 20)
                Spacer()
            }
        }
    }
}

/// 旋轉提示視圖
struct RotationHintView: View {
    enum Direction {
        case left, right
    }
    let direction: Direction

    var body: some View {
        HStack(spacing: 4) {
            if direction == .left {
                Image(systemName: "arrow.counterclockwise")
                Text("向左轉")
            } else {
                Text("向右轉")
                Image(systemName: "arrow.clockwise")
            }
        }
        .font(.subheadline)
        .fontWeight(.bold)
        .foregroundColor(.cyan)
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
        .background(Color.black.opacity(0.7))
        .cornerRadius(8)
    }
}

// MARK: - Preview

#Preview {
    ZStack {
        Color.gray
        DetectionStatusView(
            result: .empty,
            isAdjustingFocus: false
        )
        .padding()
    }
}
