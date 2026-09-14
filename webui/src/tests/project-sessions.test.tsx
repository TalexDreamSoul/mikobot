import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ProjectSessionsPanel } from "@/components/projects/ProjectSessionsPanel";
import { ProjectsView } from "@/components/projects/ProjectsView";
import i18n from "@/i18n";
import type { ChatSummary } from "@/lib/types";
import { controller, project, wrap } from "@/tests/collaboration-fixtures";

afterEach(async () => {
  cleanup();
  vi.clearAllMocks();
  await i18n.changeLanguage("en");
});

function chat(overrides: Partial<ChatSummary> & { chatId: string }): ChatSummary {
  return {
    key: `websocket:${overrides.chatId}`,
    channel: "websocket",
    createdAt: "2026-09-13T09:00:00Z",
    updatedAt: "2026-09-13T10:00:00Z",
    preview: "",
    ...overrides,
  };
}

describe("ProjectSessionsPanel", () => {
  it("lists only the conversations that run in this project and opens one", () => {
    const onOpenSession = vi.fn();
    render(
      wrap(
        <ProjectSessionsPanel
          projectId="proj-home"
          sessions={[
            chat({
              chatId: "mine",
              title: "Home chat",
              collaborationProjectId: "proj-home",
            }),
            chat({
              chatId: "other",
              title: "Other project chat",
              collaborationProjectId: "proj-other",
            }),
          ]}
          onOpenSession={onOpenSession}
        />,
      ),
    );

    expect(screen.getByText("Home chat")).toBeInTheDocument();
    expect(screen.queryByText("Other project chat")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Home chat/ }));
    expect(onOpenSession).toHaveBeenCalledWith("websocket:mine");
  });

  it("names the built-in project as the host conversations' home", () => {
    render(
      wrap(
        <ProjectSessionsPanel
          projectId="proj-home"
          sessions={[]}
          builtin
        />,
      ),
    );

    expect(screen.getByText(/The host's own conversations belong here/)).toBeInTheDocument();
    expect(screen.getByText("No conversations in this project yet.")).toBeInTheDocument();
  });
});

describe("ProjectsView conversations section", () => {
  it("puts the project's conversations in the sections and lists this project's own", () => {
    render(
      wrap(
        <ProjectsView
          projects={controller()}
          sessions={[
            chat({ chatId: "mine", title: "Home chat", collaborationProjectId: project.id }),
          ]}
          onToggleSidebar={vi.fn()}
          onOpenSession={vi.fn()}
        />,
      ),
    );

    const sections = screen.getByRole("navigation", { name: "Project sections" });
    // The board still opens first; conversations are one section away.
    expect(within(sections).getByRole("button", { name: "Tasks" }))
      .toHaveAttribute("aria-current", "page");
    fireEvent.click(within(sections).getByRole("button", { name: "Conversations" }));
    expect(screen.getByText("Home chat")).toBeInTheDocument();
  });
});
