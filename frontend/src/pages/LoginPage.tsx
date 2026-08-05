import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  AlertCircle,
  Building2,
  Eye,
  EyeOff,
  Lock,
  LogIn,
  ShieldCheck,
  User as UserIcon,
} from "lucide-react";

import { BrandLockup } from "@/components/Brand";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useAuth } from "@/context/AuthContext";
import { ApiError, authApi } from "@/lib/api";
import type { DepartmentInfo } from "@/types";

const FALLBACK_DEPARTMENTS: DepartmentInfo[] = [
  { name: "Finance", description: "Finance and Accounts" },
  { name: "HR", description: "Human Resources" },
  { name: "IT", description: "Information Technology" },
  { name: "Production", description: "Production and Manufacturing" },
  { name: "Purchase", description: "Purchase and Supply Chain" },
];

export default function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();

  const [departments, setDepartments] =
    useState<DepartmentInfo[]>(FALLBACK_DEPARTMENTS);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [department, setDepartment] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    authApi
      .listDepartments()
      .then(setDepartments)
      .catch(() => {
        /* keep the fallback list; the API may still be starting up */
      });
  }, []);

  const selected = departments.find((d) => d.name === department);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);

    if (!username.trim() || !password || !department) {
      setError("Enter your username and password, and select your department.");
      return;
    }

    setSubmitting(true);
    try {
      await login(username.trim(), password, department);
      navigate("/chat", { replace: true });
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Unable to reach the server. Please check your connection.",
      );
      setPassword("");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="relative flex min-h-dvh items-center justify-center overflow-hidden bg-background px-4 py-10">
      {/* Engineered backdrop */}
      <div className="blueprint-grid pointer-events-none absolute inset-0 opacity-[0.45]" />
      <div className="radial-glow pointer-events-none absolute inset-0" />
      <div className="pointer-events-none absolute inset-x-0 bottom-0 h-56 bg-gradient-to-t from-background to-transparent" />

      <div className="relative grid w-full max-w-5xl gap-10 lg:grid-cols-[1.05fr_1fr] lg:gap-16">
        {/* ---------------- Narrative panel ---------------- */}
        <div className="hidden flex-col justify-center lg:flex">
          <BrandLockup size="lg" subtitle="Enterprise AI Assistant" />

          <h1 className="mt-9 text-balance text-4xl font-semibold leading-[1.12] tracking-tight">
            Your department&rsquo;s knowledge,
            <span className="block bg-gradient-to-r from-primary to-ember-500 bg-clip-text text-transparent">
              answered instantly.
            </span>
          </h1>

          <p className="mt-5 max-w-md text-pretty leading-relaxed text-muted-foreground">
            Ask a question in plain language and get an answer drawn strictly
            from Aerolloy&rsquo;s approved documentation &mdash; with the source
            passage cited every time.
          </p>

          <ul className="mt-9 space-y-3.5">
            {[
              {
                title: "Strict department isolation",
                body: "You see your department's documents. Nothing else, ever.",
              },
              {
                title: "Cited, verifiable answers",
                body: "Every fact links back to the document and page it came from.",
              },
              {
                title: "It says when it does not know",
                body: "No invented figures, tolerances or approval limits.",
              },
            ].map((item) => (
              <li key={item.title} className="flex gap-3">
                <ShieldCheck className="mt-0.5 h-[1.15rem] w-[1.15rem] shrink-0 text-primary" />
                <div>
                  <div className="text-sm font-medium">{item.title}</div>
                  <div className="text-sm text-muted-foreground">
                    {item.body}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </div>

        {/* ---------------- Sign-in card ---------------- */}
        <div className="w-full self-center">
          <div className="mb-8 lg:hidden">
            <BrandLockup size="md" />
          </div>

          <div className="rounded-2xl border bg-card/85 p-7 shadow-2xl backdrop-blur-xl sm:p-8">
            <div className="mb-7">
              <h2 className="text-xl font-semibold tracking-tight">Sign in</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                All three fields must match your account.
              </p>
            </div>

            <form onSubmit={handleSubmit} className="space-y-4" noValidate>
              <div className="space-y-2">
                <Label htmlFor="username">Username</Label>
                <div className="relative">
                  <UserIcon className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    id="username"
                    name="username"
                    autoComplete="username"
                    autoFocus
                    className="pl-9"
                    placeholder="firstname.lastname"
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    disabled={submitting}
                  />
                </div>
              </div>

              <div className="space-y-2">
                <Label htmlFor="password">Password</Label>
                <div className="relative">
                  <Lock className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    id="password"
                    name="password"
                    type={showPassword ? "text" : "password"}
                    autoComplete="current-password"
                    className="pl-9 pr-10"
                    placeholder="Enter your password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    disabled={submitting}
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword((v) => !v)}
                    className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1.5 text-muted-foreground transition-colors hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    aria-label={showPassword ? "Hide password" : "Show password"}
                    tabIndex={-1}
                  >
                    {showPassword ? (
                      <EyeOff className="h-4 w-4" />
                    ) : (
                      <Eye className="h-4 w-4" />
                    )}
                  </button>
                </div>
              </div>

              <div className="space-y-2">
                <Label htmlFor="department">Department</Label>
                <Select
                  value={department}
                  onValueChange={setDepartment}
                  disabled={submitting}
                >
                  <SelectTrigger id="department" className="w-full">
                    <span className="flex min-w-0 items-center gap-2">
                      <Building2 className="h-4 w-4 shrink-0 text-muted-foreground" />
                      <SelectValue placeholder="Select your department" />
                    </span>
                  </SelectTrigger>
                  <SelectContent>
                    {departments.map((d) => (
                      <SelectItem key={d.name} value={d.name}>
                        {d.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {selected && (
                  <p className="line-clamp-2 pt-0.5 text-xs leading-relaxed text-muted-foreground">
                    {selected.description}
                  </p>
                )}
              </div>

              {error && (
                <div
                  role="alert"
                  className="flex items-start gap-2.5 rounded-lg border border-destructive/30 bg-destructive/10 px-3.5 py-3 text-sm text-destructive animate-fade-in"
                >
                  <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                  <span className="leading-relaxed">{error}</span>
                </div>
              )}

              <Button
                type="submit"
                size="lg"
                className="mt-1 w-full"
                loading={submitting}
              >
                {!submitting && <LogIn />}
                {submitting ? "Signing in..." : "Sign in"}
              </Button>
            </form>

            <div className="mt-6 flex items-start gap-2 border-t pt-5 text-xs leading-relaxed text-muted-foreground">
              <ShieldCheck className="mt-px h-3.5 w-3.5 shrink-0" />
              <span>
                Access is restricted to your own department&rsquo;s data. All
                activity is logged. Contact IT for account issues.
              </span>
            </div>
          </div>

          <p className="mt-6 text-center text-xs text-muted-foreground">
            &copy; {new Date().getFullYear()} PTC Industries Limited & Aerolloy Technologies Limited
            &middot; Enterprise Confidential
          </p>
        </div>
      </div>
    </div>
  );
}
