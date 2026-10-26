import { QueryClientProvider } from "@tanstack/react-query";
import { HashRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { Layout } from "./components/Layout";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { ActiveBranchProvider } from "./hooks/useActiveBranch";
import { queryClient } from "./lib/queryClient";
import { AccountPage } from "./pages/AccountPage";
import { AlertsPage } from "./pages/AlertsPage";
import { AuthCallbackPage } from "./pages/AuthCallbackPage";
import { BranchDetailPage } from "./pages/BranchDetailPage";
import { BranchesPage } from "./pages/BranchesPage";
import { ComparePage } from "./pages/ComparePage";
import { DashboardPage } from "./pages/DashboardPage";
import { LoginPage } from "./pages/LoginPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { OnboardingPage } from "./pages/OnboardingPage";
import { PipelinePage } from "./pages/PipelinePage";
import { ProductDetailPage } from "./pages/ProductDetailPage";
import { ProductsPage } from "./pages/ProductsPage";
import { UploadPage } from "./pages/UploadPage";

function AppRoutes() {
  const { user } = useAuth();

  return (
    <Routes>
      <Route path="/login" element={user ? <Navigate to="/" replace /> : <LoginPage />} />
      <Route path="/auth/callback" element={<AuthCallbackPage />} />
      <Route
        path="/onboarding"
        element={
          <ProtectedRoute allowWithoutOrg>
            {user?.org_id == null ? <OnboardingPage /> : <Navigate to="/" replace />}
          </ProtectedRoute>
        }
      />

      <Route
        path="/*"
        element={
          <ProtectedRoute>
            <Layout>
              <Routes>
                <Route path="/" element={<DashboardPage />} />
                <Route path="/branches" element={<BranchesPage />} />
                <Route path="/branches/:companyId/:bannerId/:branchId" element={<BranchDetailPage />} />
                <Route path="/products" element={<ProductsPage />} />
                <Route path="/compare" element={<ComparePage />} />
                <Route path="/products/:ean" element={<ProductDetailPage />} />
                <Route path="/alerts" element={<AlertsPage />} />
                <Route path="/upload" element={<UploadPage />} />
                <Route path="/account" element={<AccountPage />} />
                <Route
                  path="/pipeline"
                  element={
                    <ProtectedRoute requireOwner>
                      <PipelinePage />
                    </ProtectedRoute>
                  }
                />
                <Route path="*" element={<NotFoundPage />} />
              </Routes>
            </Layout>
          </ProtectedRoute>
        }
      />
    </Routes>
  );
}

// A HashRouter: S3 serves the one index.html and nothing else, so every route
// lives in the fragment (<bucket>/index.html#/branches). The fragment is also
// where the callback hands over the token: it never reaches a server.
export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <HashRouter>
        <AuthProvider>
          <ActiveBranchProvider>
            <AppRoutes />
          </ActiveBranchProvider>
        </AuthProvider>
      </HashRouter>
    </QueryClientProvider>
  );
}
