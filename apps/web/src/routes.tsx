import type { ComponentType } from "react";
import { Link, Navigate, type RouteObject } from "react-router-dom";
import { AppShell } from "./layouts/AppShell";
import { AuthGate } from "./layouts/AuthGate";
import { AuthLayout } from "./layouts/AuthLayout";
import { RouteErrorBoundary } from "./layouts/RouteErrorBoundary";
import { EmptyState } from "./components/ui/EmptyState";

interface PageModule {
  default: ComponentType;
}

/** Every page is loaded on first visit, so the entry bundle stays small. */
function page(load: () => Promise<PageModule>): Pick<RouteObject, "lazy"> {
  return { lazy: async () => ({ Component: (await load()).default }) };
}

function NotFoundPage() {
  return (
    <EmptyState
      title="That page does not exist"
      description="Check the address, or go back to the overview."
      action={
        <Link to="/overview" className="text-sm text-accent underline underline-offset-4">
          Back to Overview
        </Link>
      }
    />
  );
}

/**
 * The route table for every page in the app. Paths:
 *   /setup, /login                      auth screens (AuthLayout)
 *   /overview, /voice, /models, /devices core pages owned by WEB-A
 *   /chat/:conversationId?              AI Chat
 *   /tasks/:taskId?                     Tasks, with the detail drawer for one task
 *   /schedule ... /files                pages owned by WEB-B (see apps/web/README.md)
 * Optional segments keep the chat or task state mounted when the selected item changes.
 */
export const routes: RouteObject[] = [
  {
    element: <AuthGate />,
    errorElement: <RouteErrorBoundary />,
    children: [
      {
        element: <AuthLayout />,
        children: [
          { path: "setup", ...page(() => import("./pages/auth/SetupPage")) },
          { path: "login", ...page(() => import("./pages/auth/LoginPage")) },
        ],
      },
      {
        // The quick window: no sidebar, so the popup stays small. It still needs a signed-in session.
        path: "quick",
        ...page(() => import("./pages/quick/QuickAskPage")),
      },
      {
        element: <AppShell />,
        children: [
          { index: true, element: <Navigate to="/chat" replace /> },
          { path: "overview", ...page(() => import("./pages/overview/OverviewPage")) },
          { path: "chat/:conversationId?", ...page(() => import("./pages/chat/ChatPage")) },
          { path: "voice", ...page(() => import("./pages/voice/VoicePage")) },
          { path: "models", ...page(() => import("./pages/models/AIModelsPage")) },
          { path: "devices", ...page(() => import("./pages/devices/DevicesPage")) },
          { path: "tasks/:taskId?", ...page(() => import("./pages/tasks/TasksPage")) },
          { path: "schedule", ...page(() => import("./pages/schedule/SchedulePage")) },
          { path: "automation", ...page(() => import("./pages/automation/AutomationPage")) },
          { path: "explore", ...page(() => import("./pages/explore/ExplorePage")) },
          { path: "integrations", ...page(() => import("./pages/integrations/IntegrationsPage")) },
          { path: "memory", ...page(() => import("./pages/memory/MemoryPage")) },
          { path: "files", ...page(() => import("./pages/files/FilesPage")) },
          { path: "notifications", ...page(() => import("./pages/notifications/NotificationsPage")) },
          { path: "activity", ...page(() => import("./pages/activity/ActivityPage")) },
          { path: "analytics", ...page(() => import("./pages/analytics/AnalyticsPage")) },
          { path: "developer", ...page(() => import("./pages/developer/DeveloperPage")) },
          { path: "settings", ...page(() => import("./pages/settings/SettingsPage")) },
          { path: "help", ...page(() => import("./pages/help/HelpPage")) },
          { path: "*", element: <NotFoundPage /> },
        ],
      },
    ],
  },
];
