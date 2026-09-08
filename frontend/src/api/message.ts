import type { MessageInstance } from "antd/es/message/interface";

/**
 * Bridge that lets non-React modules (e.g. useMutate) use the AntD `message`
 * instance created by <App> (AntdApp). Set once by <MessageBridge /> in App.tsx.
 * We use this instead of antd's static message.* to stay compatible with
 * React 19 and AntD 5 concurrent rendering.
 */
let instance: MessageInstance | null = null;

export function setMessageInstance(message: MessageInstance): void {
  instance = message;
}

export function getMessage(): MessageInstance | null {
  return instance;
}
