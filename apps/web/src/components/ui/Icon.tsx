import type { SVGAttributes } from "react";
import { cn } from "./cn";

const PATHS = {
  overview: "M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z",
  chat: "M4 5h16v11H8l-4 4z",
  voice: "M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zM5 11a7 7 0 0 0 14 0M12 18v3",
  models: "M12 3l8 4.5v9L12 21l-8-4.5v-9zM12 12l8-4.5M12 12v9M12 12L4 7.5",
  devices: "M4 5h16v11H4zM9 20h6M12 16v4",
  tasks: "M10 6h10M10 12h10M10 18h10M4 6l1 1 2-2M4 12l1 1 2-2M4 18l1 1 2-2",
  schedule: "M4 6h16v14H4zM4 10h16M8 3v4M16 3v4",
  memory: "M12 4a8 8 0 1 0 0 16 8 8 0 0 0 0-16zM12 8v4l3 2",
  notifications: "M6 16V11a6 6 0 0 1 12 0v5l2 2H4zM10 21h4",
  explore: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM15.5 8.5l-2 5-5 2 2-5z",
  integrations: "M9 7V4M15 7V4M7 7h10v4a5 5 0 0 1-10 0zM12 16v5",
  analytics: "M4 20h16M7 16v-5M12 16V6M17 16v-8",
  activity: "M3 12h4l3-7 4 14 3-7h4",
  developer: "M9 8l-5 4 5 4M15 8l5 4-5 4M13 5l-2 14",
  settings: "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM4 12h2m12 0h2M12 4v2m0 12v2",
  help: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM9.5 9a2.5 2.5 0 0 1 5 0c0 2-2.5 2-2.5 4M12 17h.01",
  automation: "M12 4v3M12 17v3M4 12h3M17 12h3M7 7l2 2M15 15l2 2M7 17l2-2M15 9l2-2",
  files: "M4 5h6l2 2h8v12H4z",
  menu: "M4 7h16M4 12h16M4 17h16",
  close: "M6 6l12 12M18 6L6 18",
  plus: "M12 5v14M5 12h14",
  send: "M4 12l16-8-6 16-2-6z",
  download: "M12 3v12m0 0 5-5m-5 5-5-5M5 17v3h14v-3",
  stop: "M7 7h10v10H7z",
  copy: "M9 9h10v10H9zM5 15V5h10",
  refresh: "M20 12a8 8 0 1 1-2.3-5.7M20 4v5h-5",
  edit: "M4 20h4L19 9l-4-4L4 16z",
  trash: "M5 7h14M9 7V4h6v3M7 7l1 13h8l1-13",
  search: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM16 16l5 5",
  check: "M5 12l4 4 10-10",
  pin: "M8 4h8l-1.5 6 3 3H6.5l3-3zM12 13v7",
  thumbUp: "M7 11v9H4v-9zM7 11l4-7c1.5 0 2.5 1 2.5 2.5V9h5.5a2 2 0 0 1 2 2.3l-1.2 6.5A2 2 0 0 1 17.8 20H7",
  thumbDown: "M17 13V4h3v9zM17 13l-4 7c-1.5 0-2.5-1-2.5-2.5V15H6.5a2 2 0 0 1-2-2.3l1.2-6.5A2 2 0 0 1 7.7 4H17",
  archive: "M4 5h16v4H4zM5 9v10h14V9M10 13h4",
  chevronUp: "M6 15l6-6 6 6",
  chevronDown: "M6 9l6 6 6-6",
  play: "M7 4l13 8-13 8z",
  wifi: "M2 9a15 15 0 0 1 20 0M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0M12 19.5h.01",
  alert: "M12 4l9 16H3zM12 10v4M12 17h.01",
  key: "M15 7a4 4 0 1 1-3.2 6.4L5 19v-3h2v-2h2l1.6-1.6A4 4 0 0 1 15 7z",
  x: "M6 6l12 12M18 6L6 18",
} as const;

export type IconName = keyof typeof PATHS;

export interface IconProps extends Omit<SVGAttributes<SVGSVGElement>, "name"> {
  name: IconName;
}

/** Stroke icons on a 24 px grid. Decorative: the adjacent text or button label provides the name. */
export function Icon({ name, className, ...rest }: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      className={cn("size-5 shrink-0", className)}
      {...rest}
    >
      <path d={PATHS[name]} />
    </svg>
  );
}
