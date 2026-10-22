import { PageHeader } from "../../components/layout/PageHeader";
import { Badge } from "../../components/ui/Badge";
import { Card, CardTitle } from "../../components/ui/Card";
import { EmptyState } from "../../components/ui/EmptyState";
import { ErrorState } from "../../components/ui/ErrorState";
import { SkeletonList } from "../../components/ui/Skeleton";
import { useDevicesQuery } from "../../services/queries";
import type { DeviceOut } from "../../types/api";

type ResourceGroup = "folders" | "projects" | "files";

const GROUPS: Array<{ key: ResourceGroup; title: string; empty: string }> = [
  { key: "folders", title: "Approved folders", empty: "No folders are approved on this device." },
  { key: "projects", title: "Approved projects", empty: "No projects are approved on this device." },
  { key: "files", title: "Approved files", empty: "No individual files are approved on this device." },
];

function DeviceFiles({ device }: { device: DeviceOut }) {
  return (
    <Card className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <CardTitle>{device.name}</CardTitle>
        <Badge tone={device.connection_status === "online" ? "success" : "neutral"}>
          {device.connection_status === "online" ? "Online" : device.connection_status === "revoked" ? "Revoked" : "Offline"}
        </Badge>
      </div>
      {GROUPS.map((group) => {
        const labels = device.resources[group.key];
        return (
          <section key={group.key} aria-label={`${group.title} on ${device.name}`} className="flex flex-col gap-2">
            <h3 className="text-sm font-medium text-ink">
              {group.title} <span className="text-muted">({labels.length})</span>
            </h3>
            {labels.length === 0 ? (
              <p className="text-sm text-muted">{group.empty}</p>
            ) : (
              <ul className="flex flex-col gap-1 text-sm text-ink">
                {labels.map((item) => (
                  <li key={item.id} className="rounded border border-line bg-raised px-3 py-1.5">
                    {item.label}
                  </li>
                ))}
              </ul>
            )}
          </section>
        );
      })}
    </Card>
  );
}

/**
 * Files. Browsing or editing files on the server is not implemented. The desktop agent reads files
 * only from its approved list, and this page shows the labels of that list. It has no upload or
 * delete controls.
 */
export default function FilesPage() {
  const devices = useDevicesQuery();

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Files"
        description="The folders, projects and files that each paired device has approved for JARVIS. Labels only."
      />

      <Card className="flex flex-col gap-2 text-sm">
        <p className="font-medium text-ink">What this page can and cannot do</p>
        <ul className="flex list-disc flex-col gap-1 pl-5 text-muted">
          <li>Browsing or editing files on the server is not implemented yet.</li>
          <li>File reading happens only through a desktop agent, and only for items on its approved list.</li>
          <li>Paths stay on the device. Only the approved labels are shown here.</li>
        </ul>
      </Card>

      {devices.isPending ? <SkeletonList rows={2} label="Loading devices" /> : null}
      {devices.isError ? <ErrorState message={devices.error.message} onRetry={() => void devices.refetch()} /> : null}
      {devices.data && devices.data.length === 0 ? (
        <EmptyState title="No devices paired" description="Pair a desktop agent on the Devices page. Its approved folders then appear here." />
      ) : null}
      {(devices.data ?? []).map((device) => (
        <DeviceFiles key={device.id} device={device} />
      ))}
    </div>
  );
}
