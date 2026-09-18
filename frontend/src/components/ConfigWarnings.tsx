import { useEffect, useState } from "react";
import { Alert, Space, Typography } from "antd";
import { useLanguage } from "../i18n";

/**
 * The `/api/health` fields this banner reads.
 *
 * Every one of these is a *configuration* fact that the server already knows and
 * reports — the point of this component is that an operator should never have to
 * curl an endpoint to find out that a feature is switched off. A flag nobody
 * reads is not a flag.
 */
interface Health {
  notify_enabled?: boolean;
  wecom_gateway_url_is_loopback?: boolean;
  wecom_ops_chat_id_is_set?: boolean;
  image_extraction_is_simulated?: boolean;
  service_key_is_default?: boolean;
  wecom_gateway_key_is_default?: boolean;
}

/**
 * One entry per misconfiguration, each carrying the exact variable that fixes it.
 *
 * Ordered by what they cost, worst first:
 *
 *  1. `wecom_gateway_url_is_loopback` — the ERP posts every customer
 *     notification into its own container. Nothing is ever delivered and
 *     nothing errors; the only symptom is "the customer says nobody texted
 *     them". This is the whole reason the field exists.
 *  2. `wecom_ops_chat_id_is_set` — every extraction stops for review, so the
 *     parked-job queue fills on its own, and this is the only thing that
 *     announces it. Unset, the queue is silent and orders sit unprocessed.
 *  3. `image_extraction_is_simulated` — a photo or scanned PDF is not read at
 *     all; the extractor returns canned lines that happen to be real products.
 *  4. The two default-secret flags — accepted as valid credentials, committed
 *     to a public repo.
 *  5. `notify_enabled` off — the master switch, off on purpose perhaps, but
 *     worth saying out loud rather than leaving as a silence.
 */
const RULES: {
  isBad: (h: Health) => boolean;
  env: string;
  i18n: string;
}[] = [
  {
    isBad: (h) => h.wecom_gateway_url_is_loopback === true,
    env: "ERP_WECOM_GATEWAY_URL",
    i18n: "configWarnings.loopbackGateway",
  },
  {
    isBad: (h) => h.wecom_ops_chat_id_is_set === false,
    env: "ERP_WECOM_OPS_CHAT_ID",
    i18n: "configWarnings.noOpsChat",
  },
  {
    isBad: (h) => h.image_extraction_is_simulated === true,
    env: "ERP_AI_PROVIDER",
    i18n: "configWarnings.simulatedOcr",
  },
  {
    isBad: (h) => h.service_key_is_default === true,
    env: "ERP_SERVICE_KEY",
    i18n: "configWarnings.defaultServiceKey",
  },
  {
    isBad: (h) => h.wecom_gateway_key_is_default === true,
    env: "ERP_WECOM_GATEWAY_KEY",
    i18n: "configWarnings.defaultGatewayKey",
  },
  {
    isBad: (h) => h.notify_enabled === false,
    env: "ERP_NOTIFY_ENABLED",
    i18n: "configWarnings.notifyOff",
  },
];

/**
 * Warnings about the SERVER's configuration, shown in the shell.
 *
 * Three deliberate choices:
 *
 * - **Not dismissible.** A banner you can close becomes a banner nobody reads;
 *   it disappears on its own the moment the variable is set, which is the only
 *   acknowledgement worth having.
 * - **Fetched with `fetch`, not the shared `api` client.** That client has
 *   `/api/v1` as its baseURL and axios CONCATENATES — a leading `/` is not
 *   origin-absolute — so `api.get("/health")` would ask for `/api/v1/health`.
 *   `/api/health` lives at the app root and needs no token.
 * - **Silent on failure.** A shell that breaks because a diagnostics endpoint
 *   was unreachable is worse than a missing warning, so every error path
 *   renders nothing.
 */
export default function ConfigWarnings() {
  const { t } = useLanguage();
  const [health, setHealth] = useState<Health | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/health")
      .then((r) => (r.ok ? r.json() : null))
      .then((body: Health | null) => {
        if (!cancelled && body) setHealth(body);
      })
      .catch(() => {
        /* diagnostics only — never let this break the shell */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!health) return null;

  const problems = RULES.filter((rule) => {
    try {
      return rule.isBad(health);
    } catch {
      return false;
    }
  });
  if (problems.length === 0) return null;

  return (
    <Alert
      type="warning"
      showIcon
      style={{ marginBottom: 12 }}
      message={t("configWarnings.title", { count: problems.length })}
      description={
        <Space direction="vertical" size={4} style={{ display: "flex" }}>
          {problems.map((rule) => (
            <Typography.Text key={rule.env} style={{ fontSize: 12 }}>
              {t(rule.i18n)}{" "}
              <Typography.Text code style={{ fontSize: 12 }}>
                {rule.env}
              </Typography.Text>
            </Typography.Text>
          ))}
        </Space>
      }
    />
  );
}
