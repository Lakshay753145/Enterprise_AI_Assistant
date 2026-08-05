import { Building2, LogOut, Menu, Monitor, Moon, Sun } from "lucide-react";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useAuth } from "@/context/AuthContext";
import { useTheme } from "@/context/ThemeContext";
import { cn, initials } from "@/lib/utils";

export function Header({
  title,
  subtitle,
  onOpenSidebar,
  className,
}: {
  title: string;
  subtitle?: string;
  onOpenSidebar?: () => void;
  className?: string;
}) {
  const { user, logout } = useAuth();
  const { theme, setTheme } = useTheme();

  if (!user) return null;

  return (
    <header
      className={cn(
        "flex h-14 shrink-0 items-center gap-3 border-b bg-background/80 px-3 backdrop-blur-xl sm:px-5",
        className,
      )}
    >
      {onOpenSidebar && (
        <Button
          variant="ghost"
          size="icon"
          onClick={onOpenSidebar}
          className="lg:hidden"
          aria-label="Open menu"
        >
          <Menu />
        </Button>
      )}

      <div className="min-w-0 flex-1">
        <h1 className="truncate text-sm font-semibold tracking-tight">
          {title}
        </h1>
        {subtitle && (
          <p className="truncate text-xs text-muted-foreground">{subtitle}</p>
        )}
      </div>

      {/* The department badge is permanently visible: a user should never be
          uncertain about which data domain they are querying. */}
      <Badge variant="default" className="hidden shrink-0 sm:inline-flex">
        <Building2 className="h-3 w-3" />
        {user.department}
      </Badge>

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            className="h-9 gap-2 px-1.5 sm:px-2"
            aria-label="Account menu"
          >
            <Avatar className="h-7 w-7">
              <AvatarFallback>
                {initials(user.full_name || user.username)}
              </AvatarFallback>
            </Avatar>
          </Button>
        </DropdownMenuTrigger>

        <DropdownMenuContent align="end" className="w-64">
          <div className="px-2 py-2">
            <div className="truncate text-sm font-medium">
              {user.full_name || user.username}
            </div>
            <div className="truncate text-xs text-muted-foreground">
              {user.email}
            </div>
            <div className="mt-2 flex flex-wrap gap-1.5">
              <Badge variant="secondary">{user.department}</Badge>
              {user.role !== "user" && (
                <Badge variant="warning">
                  {user.role === "super_admin" ? "Super admin" : "Admin"}
                </Badge>
              )}
            </div>
          </div>

          <DropdownMenuSeparator />

          <DropdownMenuLabel>Appearance</DropdownMenuLabel>
          {(
            [
              { value: "light", label: "Light", icon: Sun },
              { value: "dark", label: "Dark", icon: Moon },
              { value: "system", label: "System", icon: Monitor },
            ] as const
          ).map((option) => (
            <DropdownMenuItem
              key={option.value}
              onClick={() => setTheme(option.value)}
              className={cn(theme === option.value && "bg-accent")}
            >
              <option.icon />
              {option.label}
            </DropdownMenuItem>
          ))}

          <DropdownMenuSeparator />

          <DropdownMenuItem
            onClick={logout}
            className="text-destructive focus:text-destructive"
          >
            <LogOut />
            Sign out
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </header>
  );
}
