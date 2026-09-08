/** Shapes for the quotations (sales) module, mirroring the backend serializer. */
export interface QuotationLine {
  id: string;
  quotation_id: string;
  line_no: number;
  product_id: string | null;
  product_display: string | null;
  quantity: number;
  unit_id: string | null;
  unit_code: string | null;
  unit_price: number | null;
}

export interface Quotation {
  id: string;
  code: string;
  customer_id: string;
  customer_name_en: string;
  customer_name_zh: string;
  status: string;
  external_name: string | null;
  service_time: string;
  pricing_cycle: string | null;
  tags: string[];
  description: string | null;
  line_count: number;
  created_at: string;
  lines: QuotationLine[];
}

/** Reused master-data shapes (kept in sync with OrdersPage). */
export interface Customer {
  id: string;
  name_en: string;
  name_zh: string;
  status: string;
}
export interface Product {
  id: string;
  sku: string;
  name_en: string;
  name_zh: string;
  default_unit_id: string | null;
  default_unit_code: string | null;
  is_active: boolean;
}
export interface Unit {
  id: string;
  code: string;
  name_en: string;
  name_zh: string;
}
