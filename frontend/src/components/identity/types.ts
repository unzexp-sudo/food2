/**
 * Shapes shared by the identity screens (docs/IDENTITY_IMPLEMENTATION_SPEC.md §2.4).
 *
 * These mirror the backend contract exactly. Where a field is nullable the UI
 * must show "unknown" rather than a guess — an unbound chat's display name is
 * routinely missing, and inventing one would be the first step towards binding
 * the wrong customer.
 */

export type IdentityKind = "wecom_external_userid" | "wecom_chat_id";

/** One row of `GET /identity/unbound` — a conversation, never a message. */
export interface UnboundChat {
  chat_key: string;
  kind: IdentityKind | null;
  value: string | null;
  display_name: string | null;
  corp_name: string | null;
  alias: string | null;
  waiting_count: number;
  last_message_at: string | null;
  document_ids: string[];
}

/** `GET /intake/documents/{id}/company-proposal` — one proposed field. */
export interface FieldDraft {
  value: string | null;
  confidence: number;
  evidence: string | null;
  method: string;
}

/** The extraction's proposal. Read-only: it is evidence, never a fact. */
export interface CompanyProposal {
  name: FieldDraft;
  address: FieldDraft;
  phone: FieldDraft;
  contact: FieldDraft;
  tax_id: FieldDraft;
  source_kind: string | null;
  raw_excerpt: string | null;
}

/** `GET /customers` item. */
export interface Customer {
  id: string;
  code: string;
  name_en: string;
  name_zh: string;
  type: string;
  contact_name: string | null;
  contact_phone: string | null;
  address: string | null;
  delivery_zone: string | null;
  notes: string | null;
  status: string;
  created_at: string;
  /** Present once someone has actually checked the address. */
  address_confirmed_at?: string | null;
}

/** `GET /intake/documents/{id}` item. */
export interface IntakeDocumentSummary {
  id: string;
  customer_id: string | null;
  source_type: string;
  original_filename: string | null;
  file_url: string | null;
  uploaded_by: string | null;
  created_at: string;
  job_id: string | null;
  job_status: string | null;
  draft_order_id: string | null;
}

/** `GET /intake/extractions/{job_id}` — only the parts the bind screen reads. */
export interface ExtractionPayload {
  raw_output?: {
    lines?: { raw_text?: string | null; cancelled?: boolean }[];
    parser_notes?: string | null;
    [key: string]: unknown;
  };
  overall_confidence?: number | null;
  parser_notes?: string | null;
}

/** `POST /identity/bind` response. */
export interface BindResult {
  id: string;
  customer_id: string;
  kind: string;
  value: string;
  status: string;
  confirmed_by: string | null;
  confirmed_at: string | null;
  released: number;
}

/** Order shape used by the delivery-confirmation panel. */
export interface OrderDeliveryState {
  id: string;
  order_number: string;
  customer_id: string;
  customer_name_en?: string;
  customer_name_zh?: string;
  status: string;
  delivery_address: string | null;
  delivery_contact_name: string | null;
  delivery_contact_phone: string | null;
  delivery_confirmed_at: string | null;
  delivery_confirmed_by: string | null;
}
