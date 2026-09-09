import { useEffect, useState } from "react";
import { Alert, InputNumber, Modal, Table, Typography } from "antd";
import { useLanguage } from "../../i18n";
import { api } from "../../api/client";
import { useMutate } from "../../api/hooks";
import { pickName } from "../../utils/format";

interface PoLine {
  id: string;
  product_name_en: string;
  product_name_zh: string;
  quantity_ordered: number;
  quantity_received: number;
  unit_code: string | null;
}

interface PoDetail {
  id: string;
  po_number: string;
  lines: PoLine[];
}

interface PoReceiveModalProps {
  open: boolean;
  poId: string | null;
  onClose: () => void;
  onSuccess?: () => void;
}

/**
 * Records goods receipt against a PO via the dedicated inbound-receipt endpoint
 * (POST /inbound-receipts), which increments each PO line's quantity_received
 * and advances PO status (sent → partially_received → received). The PO's lines
 * are fetched from GET /purchase-orders/{id} when the modal opens.
 */
export default function PoReceiveModal({
  open,
  poId,
  onClose,
  onSuccess,
}: PoReceiveModalProps) {
  const { t, lang } = useLanguage();
  const { loading: submitting, run } = useMutate();
  const [detail, setDetail] = useState<PoDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [received, setReceived] = useState<Record<string, number>>({});

  useEffect(() => {
    if (!open || !poId) {
      setDetail(null);
      setReceived({});
      return;
    }
    let cancelled = false;
    setLoading(true);
    api
      .get<PoDetail>(`/purchase-orders/${poId}`)
      .then((res) => {
        if (cancelled) return;
        setDetail(res);
        const init: Record<string, number> = {};
        for (const ln of res.lines) init[ln.id] = 0;
        setReceived(init);
      })
      .catch(() => {
        if (!cancelled) setDetail(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, poId]);

  const handleOk = () => {
    if (!poId) return;
    const lines = Object.entries(received)
      .filter(([, q]) => q > 0)
      .map(([po_line_id, quantity_received]) => ({
        po_line_id,
        quantity_received,
      }));
    if (lines.length === 0) return;
    void run(
      () => api.post("/inbound-receipts", { po_id: poId, lines }),
      {
        success: t("pages.purchaseOrders.poReceive.receiptRecorded"),
        onSuccess: () => {
          onSuccess?.();
          onClose();
        },
      },
    );
  };

  const columns = [
    {
      title: t("pages.purchaseOrders.colLineProduct"),
      key: "product",
      render: (_: unknown, r: PoLine) =>
        pickName(lang, r.product_name_en, r.product_name_zh),
    },
    {
      title: t("pages.purchaseOrders.colLineQtyOrdered"),
      key: "ordered",
      render: (_: unknown, r: PoLine) =>
        `${r.quantity_ordered} ${r.unit_code ?? ""}`.trim(),
    },
    {
      title: t("pages.purchaseOrders.colLineQtyReceived"),
      key: "already",
      render: (_: unknown, r: PoLine) =>
        `${r.quantity_received} ${r.unit_code ?? ""}`.trim(),
    },
    {
      title: t("pages.purchaseOrders.poReceive.receiveNow"),
      key: "receive",
      render: (_: unknown, r: PoLine) => {
        const remaining = Math.max(0, r.quantity_ordered - r.quantity_received);
        return (
          <InputNumber
            min={0}
            max={remaining}
            step={1}
            value={received[r.id] ?? 0}
            onChange={(v) =>
              setReceived((prev) => ({ ...prev, [r.id]: Number(v) || 0 }))
            }
            style={{ width: 120 }}
          />
        );
      },
    },
  ];

  const hasInput = Object.values(received).some((q) => q > 0);

  return (
    <Modal
      open={open}
      title={t("pages.purchaseOrders.poReceive.recordGoodsReceipt")}
      onCancel={onClose}
      onOk={handleOk}
      okText={t("common.submit")}
      okButtonProps={{ disabled: !hasInput || submitting }}
      confirmLoading={submitting}
      width={720}
      destroyOnClose
    >
      <Typography.Paragraph type="secondary">
        {detail
          ? t("pages.purchaseOrders.poReceive.poNumber", { po_number: detail.po_number })
          : ""}
      </Typography.Paragraph>
      {detail && detail.lines.length === 0 ? (
        <Alert
          type="info"
          message={t("pages.purchaseOrders.poReceive.noLines")}
        />
      ) : (
        <Table<PoLine>
          rowKey="id"
          size="small"
          loading={loading}
          dataSource={detail?.lines ?? []}
          columns={columns}
          pagination={false}
        />
      )}
    </Modal>
  );
}
