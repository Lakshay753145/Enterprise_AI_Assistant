import { lazy, Suspense, type ReactNode } from "react";
import {
  BrowserRouter,
  Navigate,
  Route,
  Routes,
  useLocation,
} from "react-router-dom";
import { Toaster } from "sonner";

import { AerolloyMark } from "@/components/Brand";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AuthProvider, useAuth } from "@/context/AuthContext";
import { ThemeProvider, useTheme } from "@/context/ThemeContext";

const LoginPage = lazy(() => import("@/pages/LoginPage"));
const ChatPage = lazy(() => import("@/pages/ChatPage"));
const DocumentsPage = lazy(() => import("@/pages/DocumentsPage"));
const AdminPage = lazy(() => import("@/pages/AdminPage"));

function SplashScreen() {
  return (
    <div className="flex h-dvh flex-col items-center justify-center gap-4 bg-background">
      <AerolloyMark className="h-11 w-11 animate-pulse text-foreground" />
      <p className="text-sm text-muted-foreground">Loading...</p>
    </div>
  );
}

function RequireAuth({ children }: { children: ReactNode }) {
  const { isAuthenticated, isLoading } = useAuth();
  const location = useLocation();

  if (isLoading) return <SplashScreen />;
  if (!isAuthenticated) {
    // Remember where they were headed so sign-in can return them there.
    return <Navigate to="/login" replace state={{ from: location }} />;
  }
  return <>{children}</>;
}

function RequireAdmin({ children }: { children: ReactNode }) {
  const { isAdmin, isLoading } = useAuth();
  if (isLoading) return <SplashScreen />;
  if (!isAdmin) return <Navigate to="/chat" replace />;
  return <>{children}</>;
}

function RedirectIfAuthenticated({ children }: { children: ReactNode }) {
  const { isAuthenticated, isLoading } = useAuth();
  if (isLoading) return <SplashScreen />;
  if (isAuthenticated) return <Navigate to="/chat" replace />;
  return <>{children}</>;
}

function AppToaster() {
  const { resolvedTheme } = useTheme();
  return (
    <Toaster
      theme={resolvedTheme}
      position="top-center"
      richColors
      closeButton
      toastOptions={{ className: "text-sm" }}
    />
  );
}

function AppRoutes() {
  return (
    <Suspense fallback={<SplashScreen />}>
      <Routes>
        <Route
          path="/login"
          element={
            <RedirectIfAuthenticated>
              <LoginPage />
            </RedirectIfAuthenticated>
          }
        />
        <Route
          path="/chat"
          element={
            <RequireAuth>
              <ChatPage />
            </RequireAuth>
          }
        />
        <Route
          path="/documents"
          element={
            <RequireAuth>
              <DocumentsPage />
            </RequireAuth>
          }
        />
        <Route
          path="/admin"
          element={
            <RequireAuth>
              <RequireAdmin>
                <AdminPage />
              </RequireAdmin>
            </RequireAuth>
          }
        />
        <Route path="/" element={<Navigate to="/chat" replace />} />
        <Route path="*" element={<Navigate to="/chat" replace />} />
      </Routes>
    </Suspense>
  );
}

export default function App() {
  return (
    <ThemeProvider>
      <AuthProvider>
        <TooltipProvider delayDuration={300} skipDelayDuration={0}>
          <BrowserRouter>
            <AppRoutes />
          </BrowserRouter>
          <AppToaster />
        </TooltipProvider>
      </AuthProvider>
    </ThemeProvider>
  );
}
