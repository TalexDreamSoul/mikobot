import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ProjectAppsPanel } from "@/components/projects/ProjectAppsPanel";
import { ProjectsView } from "@/components/projects/ProjectsView";
import i18n from "@/i18n";
import {
  approvedApp,
  availableApp,
  controller,
  driftedApp,
  detail,
  wrap,
} from "@/tests/collaboration-fixtures";

afterEach(async () => {
  cleanup();
  vi.clearAllMocks();
  await i18n.changeLanguage("en");
});

function renderPanel(overrides: {
  busyKey?: string | null;
  canManage?: boolean;
  onJoin?: (name: string, revision?: string | null) => Promise<unknown>;
  onLeave?: (name: string) => Promise<unknown>;
} = {}) {
  return render(
    <ProjectAppsPanel
      detail={detail()}
      busyKey={overrides.busyKey ?? null}
      canManage={overrides.canManage ?? true}
      onJoin={overrides.onJoin ?? vi.fn().mockResolvedValue(undefined)}
      onLeave={overrides.onLeave ?? vi.fn().mockResolvedValue(undefined)}
    />,
  );
}

function rowFor(name: string): HTMLElement {
  return screen.getByText(name).closest("li") as HTMLElement;
}

describe("ProjectAppsPanel", () => {
  it("shows each app's approval state and what it grants", () => {
    renderPanel();

    expect(within(rowFor("Studio")).getByText("Approved")).toBeTruthy();
    expect(within(rowFor("Studio")).getByText(/Approved revision rev-1/)).toBeTruthy();
    expect(within(rowFor("Studio")).getByText(/Grants 1 capabilities · poster/)).toBeTruthy();
    expect(within(rowFor("Banner kit")).getByText("Revision changed")).toBeTruthy();
    expect(within(rowFor("Charts")).getByText("Available")).toBeTruthy();

    // An approved app that has not drifted needs no re-approval.
    expect(within(rowFor("Studio")).queryByRole("button", { name: "Approve the new revision" }))
      .toBeNull();
    expect(within(rowFor("Studio")).getByRole("button", { name: "Remove" })).toBeTruthy();
    expect(within(rowFor("Charts")).getByRole("button", { name: "Add to project" })).toBeTruthy();
  });

  it("approves an app at the revision on screen, and re-approves a drifted one", () => {
    const onJoin = vi.fn().mockResolvedValue(undefined);
    renderPanel({ onJoin });

    fireEvent.click(within(rowFor("Charts")).getByRole("button", { name: "Add to project" }));
    expect(onJoin).toHaveBeenCalledWith(availableApp.name, availableApp.revision);

    fireEvent.click(
      within(rowFor("Banner kit")).getByRole("button", { name: "Approve the new revision" }),
    );
    expect(onJoin).toHaveBeenLastCalledWith(driftedApp.name, driftedApp.revision);
  });

  it("removes an approved app and hides management from members", () => {
    const onLeave = vi.fn().mockResolvedValue(undefined);
    const { unmount } = renderPanel({ onLeave });

    fireEvent.click(within(rowFor("Studio")).getByRole("button", { name: "Remove" }));
    expect(onLeave).toHaveBeenCalledWith(approvedApp.name);
    unmount();

    renderPanel({ canManage: false });
    expect(screen.queryByRole("button", { name: "Remove" })).toBeNull();
    expect(screen.getByText(/Only a project owner or an administrator/)).toBeTruthy();
  });

  it("cannot approve an app the host no longer runs", () => {
    render(
      <ProjectAppsPanel
        detail={detail({
          apps: [{ ...availableApp, enabled: false, revision: null }],
        })}
        busyKey={null}
        canManage
        onJoin={vi.fn()}
        onLeave={vi.fn()}
      />,
    );

    expect(within(rowFor("Charts")).getByRole("button", { name: "Add to project" })).toBeDisabled();
    expect(within(rowFor("Charts")).getByText(/Installed revision —/)).toBeTruthy();
  });
});

describe("ProjectsView app inventory freshness", () => {
  it("reads the host inventory again when the apps section opens", async () => {
    const refreshDetail = vi.fn().mockResolvedValue(undefined);
    render(wrap(
      <ProjectsView projects={controller({ refreshDetail })} onToggleSidebar={vi.fn()} />,
    ));

    // The board is the default section, so nothing extra is fetched for it.
    expect(refreshDetail).not.toHaveBeenCalled();

    const sections = screen.getByRole("navigation", { name: "Project sections" });
    fireEvent.click(within(sections).getByRole("button", { name: "Apps" }));
    await waitFor(() => expect(refreshDetail).toHaveBeenCalled());

    fireEvent.click(within(sections).getByRole("button", { name: "Members" }));
    await waitFor(() => expect(refreshDetail).toHaveBeenCalledTimes(1));
  });
});
