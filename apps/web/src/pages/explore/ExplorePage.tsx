import { useState } from "react";
import { PageHeader } from "../../components/layout/PageHeader";
import { Badge, type BadgeTone } from "../../components/ui/Badge";
import { Card, CardTitle } from "../../components/ui/Card";
import { EmptyState } from "../../components/ui/EmptyState";
import { ErrorState } from "../../components/ui/ErrorState";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { SkeletonList } from "../../components/ui/Skeleton";
import { Tabs } from "../../components/ui/Tabs";
import { useDebouncedValue } from "../../hooks/useDebouncedValue";
import { humanize } from "../../services/format";
import { useExploreQuery, type ExploreItem, type ExploreStatus } from "./exploreQueries";

export const EXPLORE_STATUS_LABEL: Record<ExploreStatus, string> = {
  working: "Working",
  requires_configuration: "Requires configuration",
  experimental: "Experimental",
  unsupported: "Unsupported",
};

export const EXPLORE_STATUS_TONE: Record<ExploreStatus, BadgeTone> = {
  working: "success",
  requires_configuration: "warning",
  experimental: "info",
  unsupported: "neutral",
};

function ExploreCard({ item }: { item: ExploreItem }) {
  return (
    <Card className="flex h-full flex-col gap-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <CardTitle>{item.title}</CardTitle>
          <p className="text-xs text-muted">{humanize(item.category)}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Badge tone={EXPLORE_STATUS_TONE[item.status]}>{EXPLORE_STATUS_LABEL[item.status]}</Badge>
          <Badge tone={item.enabled_for_user ? "accent" : "neutral"}>{item.enabled_for_user ? "Enabled for you" : "Not enabled"}</Badge>
        </div>
      </div>
      <p className="text-sm text-muted">{item.description}</p>
      {item.example_commands.length > 0 ? (
        <div className="flex flex-col gap-1">
          <h3 className="text-xs font-medium text-ink">Example commands</h3>
          <ul className="flex flex-col gap-1">
            {item.example_commands.map((command) => (
              <li key={command}>
                <code className="rounded bg-raised px-2 py-1 text-xs text-ink">{command}</code>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {item.requirements.length > 0 ? (
        <div className="flex flex-col gap-1">
          <h3 className="text-xs font-medium text-ink">Requirements</h3>
          <ul className="list-inside list-disc text-xs text-muted">
            {item.requirements.map((requirement) => (
              <li key={requirement}>{requirement}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </Card>
  );
}

/** Browse what JARVIS can do. Status labels say what works today. Listing an item does not allow it. */
export default function ExplorePage() {
  const [category, setCategory] = useState("all");
  const [search, setSearch] = useState("");
  const debounced = useDebouncedValue(search, 300);
  const explore = useExploreQuery({ category: category === "all" ? "" : category, q: debounced.trim() });
  const categories = explore.data?.categories ?? [];

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Explore"
        description="What JARVIS can do and what each capability needs. Finding something here does not allow it to run. Actions still follow the approval rules."
      />

      <Field label="Search capabilities">
        {(control) => <Input {...control} type="search" value={search} onChange={(event) => setSearch(event.target.value)} />}
      </Field>

      {categories.length > 0 ? (
        <Tabs
          label="Category"
          value={category}
          onChange={setCategory}
          items={[{ id: "all", label: "All" }, ...categories.map((name) => ({ id: name, label: humanize(name) }))]}
        />
      ) : null}

      {explore.isPending ? (
        <SkeletonList rows={3} label="Loading capabilities" />
      ) : explore.isError ? (
        <ErrorState message={explore.error.message} onRetry={() => void explore.refetch()} />
      ) : (explore.data?.items ?? []).length === 0 ? (
        <EmptyState title="No capabilities match" description="Try a different search or category." />
      ) : (
        <ul aria-label="Capabilities" className="grid gap-4 md:grid-cols-2">
          {(explore.data?.items ?? []).map((item) => (
            <li key={item.id}>
              <ExploreCard item={item} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
