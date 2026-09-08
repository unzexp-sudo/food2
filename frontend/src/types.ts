/** Roles exactly as in docs/AGENT_CONTRACTS.md §3. */
export type Role = "admin" | "ops" | "warehouse" | "finance" | "driver";

/** Shape of the `erp_user` object persisted in localStorage. */
export interface CurrentUser {
  id: string;
  name: string;
  role: Role;
  email?: string;
}

export function parseStoredUser(): CurrentUser | null {
  try {
    const raw = localStorage.getItem("erp_user");
    if (!raw) return null;
    const user = JSON.parse(raw) as CurrentUser;
    if (!user || typeof user.id !== "string" || typeof user.role !== "string") return null;
    return user;
  } catch {
    return null;
  }
}
