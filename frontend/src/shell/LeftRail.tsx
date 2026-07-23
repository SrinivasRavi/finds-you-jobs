// LeftRail — ports assets/shell.js renderRail(). 76px fixed rail: brand mark,
// top nav (Job board / Applications / Networking), bottom nav (Analytics),
// Settings tile. Networking is always in the rail (the CRM carries no account
// risk); the LinkedIn risk toggle gates only automated actions (FR-SET-03).

import { NavLink } from "react-router-dom";
import { useTranslation } from "react-i18next";

import logoUrl from "../assets/logo.png";
import { Icon } from "./icons";

type IconName = "search" | "briefcase" | "bookmark" | "share" | "barChart" | "file" | "settings";

interface RailItem {
  to: string;
  label: string; // i18n key
  icon: IconName;
}

const TOP: RailItem[] = [
  { to: "/jobs", label: "nav.jobBoard", icon: "search" },
  { to: "/applications", label: "nav.applications", icon: "bookmark" },
  { to: "/networking", label: "nav.networking", icon: "share" },
];
const BOTTOM: RailItem[] = [
  // Logs folded into Analytics (US-LOG-01): one surface, cost left + ledger right.
  { to: "/analytics", label: "nav.analytics", icon: "barChart" },
];
// The Dev tab (US-DEV-01) is hidden from the rail — its scenarios are better
// covered by killing `pnpm dev` (crash) + the persistent browser profile
// (logout). The /dev route + endpoints stay for future not-otherwise-testable
// scenarios (maintainer note 2026-07-09).

function Tile({ to, label, icon }: RailItem) {
  const { t } = useTranslation();
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        "flex h-[52px] w-[64px] flex-col items-center justify-center gap-1 rounded-lg transition-colors " +
        (isActive
          ? "bg-accent text-white shadow-sm"
          : "text-ink-3 hover:bg-surface-3 hover:text-ink")
      }
    >
      <Icon name={icon} size={17} strokeWidth={1.6} />
      <span className="text-center text-[10px] font-medium leading-none">{t(label)}</span>
    </NavLink>
  );
}

function BrandMark() {
  return (
    <div className="mb-3 mt-1 flex flex-col items-center gap-1" aria-label="finds-you-jobs">
      <img
        src={logoUrl}
        alt="finds-you-jobs"
        width={38}
        height={38}
        className="h-[38px] w-[38px] rounded-[10px] object-cover shadow-sm"
      />
      <span
        className="text-center text-[11px] font-semibold lowercase leading-[1.05] text-accent"
        style={{ fontFamily: '"Quicksand", sans-serif' }}
      >
        finds you jobs.
      </span>
    </div>
  );
}

export function LeftRail() {
  // Networking (the contact CRM + kanban) is always available — it carries no
  // ToS risk. The risk toggle gates only the automated LinkedIn actions
  // (discover/send), inside the surfaces (FR-SET-03 as-built 2026-07-09).
  const top = TOP;

  return (
    <nav
      className="flex flex-col items-center border-r border-border bg-surface py-2"
      aria-label="Main navigation"
    >
      <BrandMark />
      <ul className="flex flex-1 flex-col items-center gap-1" role="list">
        {top.map((it) => (
          <li key={it.to}>
            <Tile {...it} />
          </li>
        ))}
      </ul>
      <ul className="flex flex-col items-center gap-1 pb-1" role="list">
        {BOTTOM.map((it) => (
          <li key={it.to}>
            <Tile {...it} />
          </li>
        ))}
      </ul>
      <Tile to="/settings" label="nav.settings" icon="settings" />
    </nav>
  );
}
