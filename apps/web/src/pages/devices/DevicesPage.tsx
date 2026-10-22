import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { PageHeader } from "../../components/layout/PageHeader";
import { Button } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { ErrorState } from "../../components/ui/ErrorState";
import { SkeletonList } from "../../components/ui/Skeleton";
import { useDevicesQuery } from "../../services/queries";
import { DeviceCard } from "./DeviceCard";
import { PairingDialog } from "./PairingDialog";

/** Paired devices, pairing codes and per-capability permissions. Opens pairing when ?pair=1 is set. */
export default function DevicesPage() {
  const devices = useDevicesQuery();
  const [params, setParams] = useSearchParams();
  const [pairingOpen, setPairingOpen] = useState(false);

  useEffect(() => {
    if (params.get("pair") === "1") {
      setPairingOpen(true);
      setParams({}, { replace: true });
    }
  }, [params, setParams]);

  const list = devices.data ?? [];
  const pairedCount = list.filter((device) => device.pairing_status === "paired").length;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Connected devices"
        description="PCs paired with JARVIS. Each capability has its own permission, and nothing runs on a device without the permission it needs."
        actions={
          <Button variant="primary" onClick={() => setPairingOpen(true)}>
            Connect a device
          </Button>
        }
      />

      {devices.isPending ? (
        <SkeletonList rows={2} label="Loading devices" />
      ) : devices.isError ? (
        <ErrorState message={devices.error.message} onRetry={() => void devices.refetch()} />
      ) : list.length === 0 ? (
        <EmptyState
          title="No devices paired yet"
          description="Create a pairing code, then enter it in the JARVIS Windows agent on the PC you want to connect."
          action={
            <Button variant="secondary" size="sm" onClick={() => setPairingOpen(true)}>
              Create a pairing code
            </Button>
          }
        />
      ) : (
        <>
          <p className="text-sm text-muted" role="status">
            {pairedCount} paired {pairedCount === 1 ? "device" : "devices"}
          </p>
          <div className="grid gap-6 md:grid-cols-2">
            {list.map((device) => (
              <DeviceCard key={device.id} device={device} />
            ))}
          </div>
        </>
      )}

      <PairingDialog open={pairingOpen} onClose={() => setPairingOpen(false)} />
    </div>
  );
}
