import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { routes } from "../routes";
import { createTestQueryClient } from "../test/utils";
import { ToastProvider } from "../components/ui/Toast";
import { routeFetch } from "./helpers";

const USER = { id: "u1", email: "owner@example.com", display_name: "Owner", role: "owner", created_at: "2026-10-19T00:00:00Z", last_login_at: null };

function renderAt(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const queryClient = createTestQueryClient();
  render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <RouterProvider router={router} />
      </ToastProvider>
    </QueryClientProvider>,
  );
  return router;
}

describe("route table", () => {
  it("declares every core page and the WEB-B pages, with lazy loading", () => {
    const paths = JSON.stringify(routes);
    for (const path of ["overview", "chat/:conversationId?", "voice", "models", "devices", "tasks/:taskId?", "setup", "login"]) {
      expect(paths).toContain(`"${path}"`);
    }
    for (const path of ["schedule", "memory", "notifications", "explore", "integrations", "analytics", "activity", "developer", "settings", "help", "automation", "files"]) {
      expect(paths).toContain(`"${path}"`);
    }
    // The quick window sits beside the shell, so the shell is found by its pages rather than by its position.
    expect(JSON.stringify(routes[0]?.children)).toContain('"quick"');
    const shellRoute = routes[0]?.children?.find((route) => route.children?.some((child) => child.path === "overview"));
    const shell = shellRoute?.children ?? [];
    const lazyPages = shell.filter((route) => route.path && route.path !== "*" && "lazy" in route);
    expect(lazyPages.length).toBeGreaterThanOrEqual(18);
  });
});

describe("auth gate", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("sends a fresh install to the owner setup screen", async () => {
    routeFetch({ "/auth/status": () => ({ setup_required: true, authenticated: false, user: null }) });
    const router = renderAt("/overview");
    expect(await screen.findByRole("heading", { name: "Set up JARVIS" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/setup");
  });

  it("sends a signed-out viewer to sign-in and returns them to the page they asked for", async () => {
    routeFetch({ "/auth/status": () => ({ setup_required: false, authenticated: false, user: null }) });
    const router = renderAt("/tasks");
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/login");
    expect(router.state.location.state).toEqual({ from: "/tasks" });
  });

  it("keeps a signed-in viewer out of the sign-in screen", async () => {
    routeFetch({
      "/auth/status": () => ({ setup_required: false, authenticated: true, user: USER }),
      "/settings": () => ({}),
    });
    const router = renderAt("/login");
    await screen.findByRole("heading", { name: "Overview" });
    expect(router.state.location.pathname).toBe("/overview");
  });
});
