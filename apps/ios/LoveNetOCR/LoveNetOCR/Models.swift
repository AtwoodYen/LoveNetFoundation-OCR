import Foundation

// MARK: - API envelope

struct APIEnvelope<T: Decodable>: Decodable {
    let success: Bool
    let data: T?
    let message: String?
    let error_code: String?
}

// MARK: - System

struct SystemHealthPayload: Decodable {
    let status: String
    let task_manager_running: Bool?
    let workers_count: Int?
    let active_workers: Int?
    let version: String?
    let error: String?
}

// MARK: - Tasks

struct TaskSubmitPayload: Decodable {
    let task_id: String
    let document_id: String
    let status: String
    let processing_mode: String
    let priority: Int
    let created_at: String?
}

struct TaskSummaryPayload: Decodable {
    let task_id: String
    let document_id: String
    let original_filename: String?
    let status: String
    let progress: Double?
    let current_step: String?
    let created_at: String?
    let started_at: String?
    let completed_at: String?
    let processing_mode: String?
    let priority: Int?
}

struct TaskListDataPayload: Decodable {
    let tasks: [TaskSummaryPayload]
    let total: Int
    let limit: Int
    let offset: Int
}

struct OfferingFieldPayload: Decodable, Identifiable {
    let key: String
    let label: String
    let value: String
    var id: String { key }
}

struct OfferingDisplayPayload: Decodable {
    /// 後端擷取之摘要列（支持項目、奉獻日期、收據、姓名等）
    let summary: [OfferingFieldPayload]
    let hide_raw_text: Bool
    /// 舊版 API 相容
    let checked_items: [String]

    enum CodingKeys: String, CodingKey {
        case summary
        case hide_raw_text
        case fields
        case checked_items
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        hide_raw_text = try c.decodeIfPresent(Bool.self, forKey: .hide_raw_text) ?? false
        checked_items = try c.decodeIfPresent([String].self, forKey: .checked_items) ?? []
        var s = try c.decodeIfPresent([OfferingFieldPayload].self, forKey: .summary) ?? []
        if s.isEmpty {
            s = try c.decodeIfPresent([OfferingFieldPayload].self, forKey: .fields) ?? []
        }
        summary = s
    }
}

/// 任務詳情（僅解碼畫面所需欄位；其餘 JSON 鍵可忽略）
struct TaskDetailPayload: Decodable {
    let task_id: String
    let document_id: String?
    let status: String
    let progress: Double?
    let current_step: String?
    let created_at: String?
    let started_at: String?
    let completed_at: String?
    let error_message: String?
    let processing_mode: String?
    let priority: Int?
    let full_markdown: String?
    let offering_display: OfferingDisplayPayload?
}
