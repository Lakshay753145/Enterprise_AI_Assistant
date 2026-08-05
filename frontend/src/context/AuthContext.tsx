import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { ApiError, authApi, setSessionExpiredHandler, tokenStore } from "@/lib/api";
import type { User } from "@/types";

interface AuthContextValue {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  isAdmin: boolean;
  isSuperAdmin: boolean;
  login: (
    username: string,
    password: string,
    department: string,
  ) => Promise<void>;
  logout: () => void;
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  // Seed from localStorage so a refresh does not flash the login screen
  // before /auth/me resolves.
  const [user, setUser] = useState<User | null>(() => tokenStore.user);
  const [isLoading, setIsLoading] = useState(true);

  const logout = useCallback(() => {
    tokenStore.clear();
    setUser(null);
  }, []);

  // Wired here rather than in api.ts so a failed token refresh anywhere in the
  // app drops straight back to the login screen.
  useEffect(() => {
    setSessionExpiredHandler(() => setUser(null));
  }, []);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      if (!tokenStore.access) {
        setIsLoading(false);
        return;
      }
      try {
        const fresh = await authApi.me();
        if (cancelled) return;
        // Re-fetching on boot matters: a department transfer or deactivation
        // must take effect even though a valid token is still in storage.
        tokenStore.saveUser(fresh);
        setUser(fresh);
      } catch (error) {
        if (!cancelled && error instanceof ApiError && error.isAuthError) {
          tokenStore.clear();
          setUser(null);
        }
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(
    async (username: string, password: string, department: string) => {
      const tokens = await authApi.login(username, password, department);
      tokenStore.save(tokens);
      setUser(tokens.user);
    },
    [],
  );

  const refreshUser = useCallback(async () => {
    const fresh = await authApi.me();
    tokenStore.saveUser(fresh);
    setUser(fresh);
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      isAuthenticated: user !== null,
      isLoading,
      isAdmin: user?.role === "admin" || user?.role === "super_admin",
      isSuperAdmin: user?.role === "super_admin",
      login,
      logout,
      refreshUser,
    }),
    [user, isLoading, login, logout, refreshUser],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used inside an AuthProvider.");
  }
  return context;
}
