// Temporary stand-in for surfaces whose roadmap commits haven't landed yet
// (tracker, networking, analytics, settings, onboarding). Deliberately plain:
// this is rebuild scaffolding, not product UI — each route is replaced verbatim
// by the prior repository's surface when its commit lands.

export function Placeholder({ name, commit }: { name: string; commit: string }) {
  return (
    <main className="grid h-full place-content-center gap-2 p-10 text-center">
      <h1 className="text-lg font-semibold text-ink">{name}</h1>
      <p className="max-w-md text-sm text-ink-3">
        This surface lands with the {commit} commit of the rebuild. The route
        exists so the left rail keeps its final shape.
      </p>
    </main>
  );
}
