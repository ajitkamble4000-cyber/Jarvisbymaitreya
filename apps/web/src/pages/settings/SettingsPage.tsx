import { Link, useSearchParams } from "react-router-dom";
import { PageHeader } from "../../components/layout/PageHeader";
import { ErrorState } from "../../components/ui/ErrorState";
import { SkeletonList } from "../../components/ui/Skeleton";
import { Tabs } from "../../components/ui/Tabs";
import { useAuth } from "../../hooks/useAuth";
import { useSettingsQuery } from "../../services/queries";
import { AiSection } from "./AiSection";
import { AppearanceSection } from "./AppearanceSection";
import { AutomationSection } from "./AutomationSection";
import { MemorySection } from "./MemorySection";
import { NotificationsSection } from "./NotificationsSection";
import { PrivacySection } from "./PrivacySection";
import { SecuritySection } from "./SecuritySection";
import { UsersSection } from "./UsersSection";
import { effectiveSettingsTab, parseSettingsTab, settingsTabsFor, type SettingsTabId } from "./settingsModel";
import { VoiceSection } from "./VoiceSection";

/**
 * Settings in tabs. Each control saves through PATCH /settings and only the changed keys are sent.
 * The active tab lives in the URL (?tab=), so that other pages can link straight to one.
 */
export default function SettingsPage() {
  const settings = useSettingsQuery();
  const [params, setParams] = useSearchParams();
  const auth = useAuth();
  const isOwner = auth.user?.role === "owner";
  const tab: SettingsTabId = effectiveSettingsTab(parseSettingsTab(params.get("tab")), isOwner);
  const doc = settings.data;

  const selectTab = (id: string): void => {
    setParams({ tab: id }, { replace: true });
  };

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Settings"
        description="How JARVIS works and looks for you. Changes are saved to your account. Passwords and provider keys are never shown here."
      />

      <Tabs label="Settings sections" value={tab} onChange={selectTab} items={settingsTabsFor(isOwner).map((item) => ({ id: item.id, label: item.label }))} />

      {settings.isPending ? (
        <SkeletonList rows={4} label="Loading settings" />
      ) : settings.isError || !doc ? (
        <ErrorState message={settings.error?.message ?? "The settings could not be loaded."} onRetry={() => void settings.refetch()} />
      ) : (
        <div role="tabpanel" id={`settings-panel-${tab}`} aria-labelledby={`tab-${tab}`}>
          {tab === "ai" ? <AiSection ai={doc.ai} /> : null}
          {tab === "voice" ? <VoiceSection voice={doc.voice} /> : null}
          {tab === "appearance" ? <AppearanceSection appearance={doc.appearance} /> : null}
          {tab === "automation" ? <AutomationSection automation={doc.automation} timezone={doc.timezone} /> : null}
          {tab === "notifications" ? <NotificationsSection notifications={doc.notifications} timezone={doc.timezone} /> : null}
          {tab === "memory" ? <MemorySection memory={doc.memory} /> : null}
          {tab === "security" ? <SecuritySection /> : null}
          {tab === "privacy" ? <PrivacySection privacy={doc.privacy} /> : null}
          {tab === "users" && isOwner ? <UsersSection /> : null}
        </div>
      )}

      {doc ? (
        <p className="text-xs text-muted">
          Schedules and memories have their own pages: <Link to="/schedule" className="text-accent underline underline-offset-4">Schedule</Link> and{" "}
          <Link to="/memory" className="text-accent underline underline-offset-4">Memory</Link>.
        </p>
      ) : null}
    </div>
  );
}
