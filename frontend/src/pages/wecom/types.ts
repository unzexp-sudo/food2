/** WeCom Gateway entity shapes (docs/WECOM_CONTRACTS.md §3, §5-§7). */

export interface WeComMessage {
  id: string;
  msgid: string;
  seq: number | null;
  direction: string;
  external_userid: string | null;
  chat_id: string | null;
  sender_userid: string | null;
  msgtype: string;
  content_text: string | null;
  file_url: string | null;
  source_type: string | null;
  customer_id: string | null;
  bind_status: string;
  status: string;
  intake_job_id: string | null;
  document_id: string | null;
  reply_to_msgid: string | null;
  error: string | null;
  received_at: string | null;
  created_at: string;
}

export interface WeComContact {
  id: string;
  external_userid: string;
  name: string | null;
  alias: string | null;
  corp_name: string | null;
  is_staff: boolean;
  staff_userid: string | null;
  customer_id: string | null;
  bind_method: string | null;
  bind_confidence: number | null;
  created_at: string;
  updated_at: string;
}

export interface WeComOutbound {
  id: string;
  template: string;
  to_type: string | null;
  to_id: string | null;
  customer_id: string | null;
  order_id: string | null;
  locale: string;
  rendered_text: string | null;
  status: string;
  error: string | null;
  created_at: string;
}

export interface WeComHealth {
  status: string;
  mode: string;
  erp_reachable: boolean;
  archive_enabled: boolean;
  contacts: number;
  messages: number;
  time: string;
}

/** ERP customer, as returned by GET /api/v1/customers (AGENT_CONTRACTS §4). */
export interface ErpCustomer {
  id: string;
  code: string;
  name_en: string;
  name_zh: string;
  type: string;
  status: string;
}
