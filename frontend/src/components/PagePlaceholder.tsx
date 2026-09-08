import { Card, Typography } from "antd";
import { useLanguage } from "../i18n";

/**
 * Standard placeholder for every route in AGENT_CONTRACTS §7.
 * Screen agents: replace the page component's body, keep the file name.
 */
export default function PagePlaceholder({ titleKey }: { titleKey: string }) {
  const { t } = useLanguage();
  return (
    <Card title={t(titleKey)}>
      <Typography.Text type="secondary">{t("common.underConstruction")}</Typography.Text>
    </Card>
  );
}
