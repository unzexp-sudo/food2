import { useEffect } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import enUS from "antd/locale/en_US";
import zhCN from "antd/locale/zh_CN";
import "./i18n";
import { LanguageProvider, useLanguage } from "./i18n";
import { AppRoutes } from "./router";
import { setMessageInstance } from "./api/message";

/** Captures the AntdApp message instance for useMutate (see src/api/message.ts). */
function MessageBridge() {
  const { message } = AntdApp.useApp();
  useEffect(() => {
    setMessageInstance(message);
  }, [message]);
  return null;
}

function Shell() {
  const { lang } = useLanguage();
  return (
    <ConfigProvider locale={lang === "zh" ? zhCN : enUS}>
      <AntdApp>
        <MessageBridge />
        <AppRoutes />
      </AntdApp>
    </ConfigProvider>
  );
}

export default function App() {
  return (
    <LanguageProvider>
      <Shell />
    </LanguageProvider>
  );
}
