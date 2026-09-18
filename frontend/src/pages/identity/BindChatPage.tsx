import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Button, Card, Space, Typography } from "antd";
import { ArrowLeftOutlined, LinkOutlined } from "@ant-design/icons";
import { useLanguage } from "../../i18n";
import BindCustomerDrawer from "../../components/identity/BindCustomerDrawer";

/**
 * The deep-linkable entry point to binding (`/identity/chats/bind?kind=&value=
 * &chat_key=`), reached from the unbound-chats queue.
 *
 * The screen itself now lives in `BindCustomerDrawer`, because the same
 * decision has to be available from inside the intake inbox — where the
 * operator actually meets the wall — and a routed page cannot be opened from
 * there without losing their place. This page keeps the URL working, so
 * bookmarks, the queue's Bind action and the sidebar badge are unaffected.
 */
export default function BindChatPage() {
  const { t } = useLanguage();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [open, setOpen] = useState(true);

  const kind = searchParams.get("kind");
  const value = searchParams.get("value");
  const chatKey = searchParams.get("chat_key");

  // Closing the drawer returns to the queue: the operator came here for one
  // decision, and "done" and "never mind" both mean going back.
  const close = () => {
    setOpen(false);
    navigate("/identity/chats");
  };

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Card>
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Space wrap>
            <Button icon={<ArrowLeftOutlined />} onClick={close}>
              {t("pages.identity.bind.back")}
            </Button>
            <Typography.Text strong>{t("pages.identity.bind.title")}</Typography.Text>
          </Space>
          <Typography.Text type="secondary">
            {t("pages.identity.bind.pageHint")}
          </Typography.Text>
          {!open ? (
            <Button type="primary" icon={<LinkOutlined />} onClick={() => setOpen(true)}>
              {t("pages.identity.bind.bindAction")}
            </Button>
          ) : null}
        </Space>
      </Card>

      <BindCustomerDrawer
        open={open}
        kind={kind}
        value={value}
        chatKey={chatKey}
        onClose={close}
      />
    </Space>
  );
}
