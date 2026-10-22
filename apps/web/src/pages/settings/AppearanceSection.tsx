import { useState } from "react";
import { Button } from "../../components/ui/Button";
import { Field } from "../../components/ui/Field";
import { Icon } from "../../components/ui/Icon";
import { IconButton } from "../../components/ui/IconButton";
import { Input, Select } from "../../components/ui/Input";
import { Switch } from "../../components/ui/Switch";
import { NAV_ITEMS, moveNavItem, orderNavItems, visibleNavItems } from "../../layouts/nav";
import { uiStore, useUiStore } from "../../stores/ui-store";
import type { AppearanceSettings, DashboardWidgets } from "../../types/api";
import { ImmediateSwitch, SettingsCard } from "./SettingsShared";
import { isHexColor } from "./settingsModel";
import { useSaveSettings } from "./settingsSave";

const THEME_OPTIONS = [
  { value: "dark", label: "Dark" },
  { value: "light", label: "Light" },
  { value: "system", label: "Match the system" },
  { value: "high-contrast", label: "High contrast" },
] as const;
const ORB_OPTIONS = [
  { value: "orb", label: "Orb" },
  { value: "ring", label: "Ring" },
  { value: "minimal", label: "Minimal" },
] as const;
const MOTION_INTENSITY_OPTIONS = [
  { value: "off", label: "Off" },
  { value: "low", label: "Low" },
  { value: "normal", label: "Normal" },
  { value: "high", label: "High" },
] as const;
const REDUCED_MOTION_OPTIONS = [
  { value: "system", label: "Follow the system setting" },
  { value: "on", label: "Always reduce motion" },
  { value: "off", label: "Never reduce motion" },
] as const;
const FONT_OPTIONS = [
  { value: "small", label: "Small" },
  { value: "medium", label: "Medium" },
  { value: "large", label: "Large" },
] as const;
const WIDGET_LABELS: Record<keyof DashboardWidgets, string> = {
  status: "Assistant status",
  devices: "Devices",
  tasks: "Tasks",
  activity: "Activity",
  schedule: "Schedule",
  quick_actions: "Quick actions",
};

export function AppearanceSection({ appearance }: { appearance: AppearanceSettings }) {
  return <AppearanceForm key={JSON.stringify(appearance)} appearance={appearance} />;
}

function AppearanceForm({ appearance }: { appearance: AppearanceSettings }) {
  const save = useSaveSettings();
  const sidebarOrder = useUiStore((state) => state.sidebarOrder);
  const [accent, setAccent] = useState(appearance.accent_color);
  const accentValid = isHexColor(accent);
  const accentChanged = accent !== appearance.accent_color;

  const hidden = appearance.sidebar_hidden;
  const ordered = orderNavItems(sidebarOrder, NAV_ITEMS);
  const orderIds = ordered.map((item) => item.id);
  const visibleCount = visibleNavItems(sidebarOrder, hidden, NAV_ITEMS).length;

  const setHidden = (id: string, visible: boolean): void => {
    const next = visible ? hidden.filter((item) => item !== id) : [...hidden, id];
    save.mutate({ appearance: { sidebar_hidden: next } });
  };

  const setWidget = (key: keyof DashboardWidgets, value: boolean): void => {
    save.mutate({ appearance: { dashboard_widgets: { ...appearance.dashboard_widgets, [key]: value } } });
  };

  const saveAccent = (): void => {
    if (!accentValid) return;
    save.mutate({ appearance: { accent_color: accent } });
  };

  return (
    <div className="flex flex-col gap-6">
      <SettingsCard title="Look" description="Saved as soon as you change them. They follow you to every device that signs in.">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Theme">
            {(control) => (
              <Select
                {...control}
                disabled={save.isPending}
                value={appearance.theme}
                onChange={(event) => save.mutate({ appearance: { theme: event.target.value as AppearanceSettings["theme"] } })}
              >
                {THEME_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Assistant orb style">
            {(control) => (
              <Select
                {...control}
                disabled={save.isPending}
                value={appearance.orb_style}
                onChange={(event) => save.mutate({ appearance: { orb_style: event.target.value as AppearanceSettings["orb_style"] } })}
              >
                {ORB_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Animation intensity">
            {(control) => (
              <Select
                {...control}
                disabled={save.isPending}
                value={appearance.animation_intensity}
                onChange={(event) =>
                  save.mutate({ appearance: { animation_intensity: event.target.value as AppearanceSettings["animation_intensity"] } })
                }
              >
                {MOTION_INTENSITY_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Reduced motion">
            {(control) => (
              <Select
                {...control}
                disabled={save.isPending}
                value={appearance.reduced_motion}
                onChange={(event) => save.mutate({ appearance: { reduced_motion: event.target.value as AppearanceSettings["reduced_motion"] } })}
              >
                {REDUCED_MOTION_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Font size">
            {(control) => (
              <Select
                {...control}
                disabled={save.isPending}
                value={appearance.font_size}
                onChange={(event) => save.mutate({ appearance: { font_size: event.target.value as AppearanceSettings["font_size"] } })}
              >
                {FONT_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        </div>

        <div className="flex flex-col gap-3">
          <Field label="Accent colour" hint="A hex colour such as #22d3ee." error={accentValid ? null : "Enter a colour as #rrggbb."}>
            {(control) => (
              <div className="flex flex-wrap items-center gap-3">
                <input
                  type="color"
                  aria-label="Pick accent colour"
                  value={accentValid ? accent : appearance.accent_color}
                  onChange={(event) => setAccent(event.target.value)}
                  className="h-10 w-14 cursor-pointer rounded-lg border border-line bg-raised p-1"
                />
                <Input {...control} className="w-40 font-mono" invalid={!accentValid} value={accent} onChange={(event) => setAccent(event.target.value)} />
                <span
                  aria-hidden="true"
                  data-testid="accent-preview"
                  className="inline-flex h-10 items-center rounded-lg px-4 text-sm font-medium text-on-accent"
                  style={{ backgroundColor: accentValid ? accent : appearance.accent_color }}
                >
                  Preview
                </span>
              </div>
            )}
          </Field>
          <div className="flex justify-end">
            <Button variant="primary" size="sm" disabled={!accentChanged || !accentValid} loading={save.isPending} onClick={saveAccent}>
              Save colour
            </Button>
          </div>
        </div>
      </SettingsCard>

      <SettingsCard title="Sidebar" description="Hide the items you do not use. The order is kept on this device only.">
        <ul aria-label="Sidebar items" className="flex flex-col gap-2">
          {ordered.map((item, index) => {
            const isHidden = hidden.includes(item.id);
            const isLastVisible = visibleCount <= 1 && !isHidden;
            return (
              <li key={item.id} className="flex items-center gap-3 rounded-lg border border-line bg-raised px-3 py-2">
                <Icon name={item.icon} className="size-4 text-muted" />
                <div className="min-w-0 flex-1">
                  <Switch
                    label={item.label}
                    checked={!isHidden}
                    disabled={save.isPending || isLastVisible}
                    onCheckedChange={(visible) => setHidden(item.id, visible)}
                  />
                </div>
                <IconButton
                  label={`Move ${item.label} up`}
                  icon={<Icon name="chevronUp" className="size-4" />}
                  disabled={index === 0}
                  onClick={() => uiStore.setPreferences({ sidebarOrder: moveNavItem(orderIds, item.id, -1) })}
                />
                <IconButton
                  label={`Move ${item.label} down`}
                  icon={<Icon name="chevronDown" className="size-4" />}
                  disabled={index === ordered.length - 1}
                  onClick={() => uiStore.setPreferences({ sidebarOrder: moveNavItem(orderIds, item.id, 1) })}
                />
              </li>
            );
          })}
        </ul>
      </SettingsCard>

      <SettingsCard title="Overview widgets" description="Which cards the Overview page shows.">
        {(Object.keys(WIDGET_LABELS) as Array<keyof DashboardWidgets>).map((key) => (
          <ImmediateSwitch
            key={key}
            label={WIDGET_LABELS[key]}
            checked={appearance.dashboard_widgets[key]}
            pending={save.isPending}
            onChange={(value) => setWidget(key, value)}
          />
        ))}
      </SettingsCard>
    </div>
  );
}
