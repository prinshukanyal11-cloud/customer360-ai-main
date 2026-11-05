import { NavLink, Outlet } from "react-router-dom";
import {
  LayoutDashboard,
  Users,
  BarChart3,
  HeartPulse,
  Megaphone,
  Bot,
  Boxes,
  Gauge,
  Download,
  Database,
  Settings,
  ShieldAlert,
  ChartPie,
} from "lucide-react";

const NAV_ITEMS = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard },
  { to: "/customers", label: "Customers", icon: Users },
  { to: "/at-risk", label: "At Risk", icon: ShieldAlert },
  { to: "/segmentation", label: "Segmentation", icon: BarChart3 },
  { to: "/analytics", label: "Analytics", icon: ChartPie },
  { to: "/retention", label: "Retention", icon: HeartPulse },
  { to: "/campaigns", label: "Campaigns", icon: Megaphone },
  { to: "/ai/analyst", label: "AI Analyst", icon: Bot },
  { to: "/models", label: "Models", icon: Boxes },
  { to: "/monitoring", label: "Monitoring", icon: Gauge },
  { to: "/reports", label: "Reports", icon: Download },
  { to: "/data/upload", label: "Data", icon: Database },
  { to: "/settings", label: "Settings", icon: Settings },
];

export function MainLayout() {
  return (
    <div className="flex h-screen bg-slate-50">
      <aside className="w-64 shrink-0 border-r bg-white">
        <div className="flex h-16 items-center border-b px-6">
          <span className="text-lg font-semibold text-slate-900">Customer360 AI</span>
        </div>
        <nav className="space-y-1 p-3">
          {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors ${
                  isActive
                    ? "bg-slate-900 text-white"
                    : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
                }`
              }
            >
              <Icon className="h-4 w-4" />
              {label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <main className="flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  );
}