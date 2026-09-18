import { Alert } from "antd";
import { useLanguage } from "../i18n";
import { WECOM_GATEWAY_URL, WECOM_GATEWAY_URL_IS_UNCONFIGURED } from "../api/client";

/**
 * Shown when a WeCom admin screen cannot read the gateway.
 *
 * The message has to name the right cause, because two very different faults
 * look identical from the browser — a rejected CORS preflight and a service that
 * is genuinely down both surface as one opaque network error — and only one of
 * them is fixed by restarting something.
 *
 * There is a third cause that is worse than either, because it cannot be
 * diagnosed from the error at all: **the bundle was compiled without
 * `VITE_WECOM_GATEWAY_URL`**, so every browser is calling the loopback fallback
 * and the gateway could not possibly answer. That is what production did, and
 * the old message made it worse by instructing the operator to start the
 * gateway on their own machine. `import.meta.env.PROD` separates the two
 * readings of the same value: in a dev build the loopback default is correct and
 * nothing is wrong; in a deployed build it is a build-configuration fault.
 *
 * Kept in one component rather than repeated per page, so the three WeCom
 * screens cannot drift into describing the same failure differently.
 */
export default function GatewayUnreachableAlert() {
  const { t } = useLanguage();

  const unconfigured = WECOM_GATEWAY_URL_IS_UNCONFIGURED && import.meta.env.PROD;

  return (
    <Alert
      type="warning"
      showIcon
      style={{ marginBottom: 12 }}
      message={
        unconfigured
          ? t("pages.wecom.gatewayNotConfigured", { url: WECOM_GATEWAY_URL })
          : t("pages.wecom.gatewayUnreachable", { url: WECOM_GATEWAY_URL })
      }
    />
  );
}
