import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router";
import { api, setManagementProjectId } from "@/lib/api";
import { ProjectContext, type DashboardProject } from "@/contexts/project-context";
import { useProfileScope } from "@/contexts/useProfileScope";

/**
 * The project write/read target for every inherited Hermes dashboard page.
 *
 * A selected project is reflected in the URL, sent on every API/WS request,
 * and maps the inherited profile-aware surfaces onto that project's isolated
 * PM runtime.  The server remains authoritative and rejects unauthorized
 * project ids; the browser never manufactures a project list.
 */
export function ProjectProvider({ children }: { children: ReactNode }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [projects, setProjects] = useState<DashboardProject[]>([]);
  const [projectId, setProjectIdState] = useState(() => searchParams.get("project_id") ?? "");
  const [loading, setLoading] = useState(true);
  const { setProfile } = useProfileScope();

  const applyProject = useCallback((id: string, available = projects) => {
    const next = available.find((project) => project.id === id);
    const resolvedId = next?.id ?? "";
    setManagementProjectId(resolvedId);
    // Core Hermes endpoints already understand profile isolation.  Binding the
    // project to its dedicated PM profile makes Chat, Sessions, Files, Config,
    // Models, Skills and other inherited pages operate in the same boundary.
    setProfile(next?.profile ?? "");
    setProjectIdState(resolvedId);
    // A number of inherited pages load their data on mount. A full route
    // navigation is intentional here: it prevents a page from continuing to
    // render cached rows from the prior project's profile after a switch.
    const target = new URL(window.location.href);
    if (resolvedId) target.searchParams.set("project_id", resolvedId);
    else target.searchParams.delete("project_id");
    if (next?.profile) target.searchParams.set("profile", next.profile);
    else target.searchParams.delete("profile");
    window.location.assign(target.pathname + target.search + target.hash);
  }, [projects, setProfile, setSearchParams]);

  useEffect(() => {
    let cancelled = false;
    api.getDashboardProjects()
      .then((response) => {
        if (cancelled) return;
        const available = response.projects;
        setProjects(available);
        const requested = searchParams.get("project_id") ?? "";
        const initial = available.some((project) => project.id === requested)
          ? requested
          : available[0]?.id ?? "";
        const selected = available.find((project) => project.id === initial);
        setManagementProjectId(initial);
        setProfile(selected?.profile ?? "");
        setProjectIdState(initial);
        setLoading(false);
      })
      .catch(() => {
        // Keep inherited Hermes available if PM-OS is unavailable. No project
        // header is emitted until the server supplies an authorized choice.
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
    // Load once after authentication. URL changes are handled by the explicit
    // switcher, avoiding a repeated network request per core-tab navigation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const setProjectId = useCallback((id: string) => applyProject(id), [applyProject]);

  // ProfileProvider also maintains its own URL projection. Re-assert the
  // project identity after it updates ``profile`` so a core-tab deep link
  // always retains both the project id (authorization) and PM runtime.
  useEffect(() => {
    if (!projectId || searchParams.get("project_id") === projectId) return;
    setSearchParams((previous) => {
      const params = new URLSearchParams(previous);
      params.set("project_id", projectId);
      return params;
    }, { replace: true });
  }, [projectId, searchParams, setSearchParams]);

  const value = useMemo(() => ({ projectId, projects, loading, setProjectId }), [projectId, projects, loading, setProjectId]);
  return (
    <ProjectContext.Provider value={value}>
      {loading ? (
        <div
          aria-live="polite"
          className="flex h-dvh items-center justify-center bg-background-base px-6 text-text-secondary"
          role="status"
        >
          Loading project workspace…
        </div>
      ) : children}
    </ProjectContext.Provider>
  );
}
