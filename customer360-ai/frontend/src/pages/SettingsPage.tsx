import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { authService } from "@/services/auth";
import type { User } from "@/types";

export function SettingsPage() {
  const navigate = useNavigate();
  const { data: user } = useQuery<User>({
    queryKey: ["me"],
    queryFn: authService.me,
  });

  // authService.logout revokes the refresh token server-side before clearing
  // local storage. Clearing storage alone would leave a usable token behind.
  const logout = async () => {
    await authService.logout();
    navigate("/login");
  };

  return (
    <div className="p-8">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold text-slate-900">Settings</h1>
        <p className="mt-1 text-sm text-slate-500">Account and organization settings.</p>
      </header>

      <div className="max-w-lg rounded-lg border bg-white p-6">
        <h2 className="text-sm font-medium text-slate-700">Your account</h2>
        <dl className="mt-4 space-y-3 text-sm">
          <div className="flex justify-between">
            <dt className="text-slate-500">Email</dt>
            <dd className="text-slate-900">{user?.email ?? "…"}</dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-slate-500">Name</dt>
            <dd className="text-slate-900">{user?.full_name ?? "—"}</dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-slate-500">Role</dt>
            <dd className="text-slate-900">{user?.role ?? "…"}</dd>
          </div>
        </dl>
        <button
          onClick={() => void logout()}
          className="mt-6 rounded-md border border-red-200 px-4 py-2 text-sm font-medium text-red-700 hover:bg-red-50"
        >
          Sign out
        </button>
      </div>
    </div>
  );
}