import type { ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { MainLayout } from "@/layouts/MainLayout";
import { LoginPage } from "@/pages/LoginPage";
import { RegisterPage } from "@/pages/RegisterPage";
import { DashboardPage } from "@/pages/DashboardPage";
import { CustomersPage } from "@/pages/CustomersPage";
import { CustomersDetailPage } from "@/pages/CustomersDetailPage";
import { DataUploadPage } from "@/pages/DataUploadPage";
import { SegmentationPage } from "@/pages/SegmentationPage";
import { ModelManagementPage } from "@/pages/ModelManagementPage";
import { MonitoringPage } from "@/pages/MonitoringPage";
import { ReportsPage } from "@/pages/ReportsPage";
import { AtRiskBoardPage } from "@/pages/AtRiskBoardPage";
import { RetentionPage } from "@/pages/RetentionPage";
import { AnalyticsPage } from "@/pages/AnalyticsPage";
import { CampaignsPage } from "@/pages/CampaignsPage";
import { SettingsPage } from "@/pages/SettingsPage";
import { AiAnalystPage } from "@/pages/AiAnalystPage";

function RequireAuth({ children }: { children: ReactNode }) {
  const token = localStorage.getItem("access_token");
  if (!token) {
    return <Navigate to="/login" replace />;
  }
  return <>{children}</>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route
        path="/"
        element={
          <RequireAuth>
            <MainLayout />
          </RequireAuth>
        }
      >
        <Route index element={<DashboardPage />} />
        <Route path="customers" element={<CustomersPage />} />
        <Route path="customers/:id" element={<CustomersDetailPage />} />
        <Route path="at-risk" element={<AtRiskBoardPage />} />
        <Route path="retention" element={<RetentionPage />} />
        <Route path="campaigns" element={<CampaignsPage />} />
        <Route path="analytics" element={<AnalyticsPage />} />
        <Route path="data/upload" element={<DataUploadPage />} />
        <Route path="segmentation" element={<SegmentationPage />} />
        <Route path="models" element={<ModelManagementPage />} />
        <Route path="monitoring" element={<MonitoringPage />} />
        <Route path="reports" element={<ReportsPage />} />
        <Route path="ai/analyst" element={<AiAnalystPage />} />
        <Route path="settings" element={<SettingsPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}