import { useState } from "react";
import { Alert, App, Button, Card, Form, Input, Segmented, Typography } from "antd";
import { GlobalOutlined, LockOutlined, MailOutlined } from "@ant-design/icons";
import { Navigate, useNavigate } from "react-router-dom";
import { api, getApiError } from "../../api/client";
import { useLanguage, type Lang } from "../../i18n";
import type { CurrentUser } from "../../types";

interface LoginFormValues {
  email: string;
  password: string;
}

interface LoginResponse {
  token: string;
  user: CurrentUser;
}

const DEMO_ACCOUNTS = [
  "admin@erp.local",
  "ops@erp.local",
  "warehouse@erp.local",
  "finance@erp.local",
  "driver@erp.local",
];

export default function LoginPage() {
  const { t, lang, setLang } = useLanguage();
  const { message } = App.useApp();
  const navigate = useNavigate();
  const [submitting, setSubmitting] = useState(false);
  const token = localStorage.getItem("erp_token");

  const onFinish = async (values: LoginFormValues) => {
    setSubmitting(true);
    try {
      const res = await api.post<LoginResponse>("/auth/login", values);
      localStorage.setItem("erp_token", res.token);
      localStorage.setItem("erp_user", JSON.stringify(res.user));
      message.success(t("login.success"));
      navigate("/intake", { replace: true });
    } catch (error) {
      message.error(getApiError(error) ?? t("login.failed"));
    } finally {
      setSubmitting(false);
    }
  };

  if (token) {
    return <Navigate to="/intake" replace />;
  }

  return (
    <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column" }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "flex-end",
          gap: 8,
          padding: 16,
          color: "rgba(0, 0, 0, 0.55)",
        }}
      >
        <GlobalOutlined />
        <Segmented<Lang>
          value={lang}
          onChange={(value) => setLang(value)}
          options={[
            { label: "English", value: "en" },
            { label: "中文", value: "zh" },
          ]}
        />
      </div>
      <div
        style={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: 16,
          padding: 24,
        }}
      >
        <Card style={{ width: 380 }}>
          <Typography.Title level={3} style={{ marginTop: 0 }}>
            {t("login.title")}
          </Typography.Title>
          <Typography.Text type="secondary">{t("login.subtitle")}</Typography.Text>
          <Form<LoginFormValues> layout="vertical" onFinish={onFinish} style={{ marginTop: 16 }}>
            <Form.Item
              name="email"
              label={t("login.email")}
              rules={[{ required: true, message: t("login.email") }]}
            >
              <Input prefix={<MailOutlined />} placeholder={t("login.emailPlaceholder")} />
            </Form.Item>
            <Form.Item
              name="password"
              label={t("login.password")}
              rules={[{ required: true, message: t("login.passwordRequired") }]}
            >
              <Input.Password prefix={<LockOutlined />} placeholder={t("login.passwordPlaceholder")} />
            </Form.Item>
            <Button type="primary" htmlType="submit" block loading={submitting}>
              {t("login.submit")}
            </Button>
          </Form>
        </Card>
        <Card style={{ width: 380 }} size="small">
          <Typography.Text strong>{t("login.demoTitle")}</Typography.Text>
          <div style={{ marginTop: 4 }}>
            {DEMO_ACCOUNTS.map((account) => (
              <Typography.Paragraph key={account} copyable style={{ marginBottom: 2 }}>
                <Typography.Text code>{account}</Typography.Text>
              </Typography.Paragraph>
            ))}
          </div>
          <Alert type="info" showIcon message={t("login.demoPassword", { password: "erp123" })} />
        </Card>
      </div>
    </div>
  );
}
