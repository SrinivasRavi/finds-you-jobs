// App layout — the 76px rail + main column grid the prototype uses
// (`grid h-screen grid-cols-[76px_1fr]`). Routed surfaces render into <Outlet>.

import { Outlet } from "react-router-dom";

import { LeftRail } from "./LeftRail";

export function Layout() {
  return (
    <div className="grid h-screen grid-cols-[76px_1fr] overflow-hidden bg-canvas">
      <LeftRail />
      <div className="flex min-h-0 flex-col overflow-hidden">
        <Outlet />
      </div>
    </div>
  );
}
