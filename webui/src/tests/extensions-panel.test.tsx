import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ExtensionsPanel } from "@/components/projects/ExtensionsPanel";
import type { CollaborationExtensionSettings, CollaborationProjectPayload } from "@/lib/types";

const initialAvailable = {
  skills: [
    { id: "architecture", name: "Architecture", description: "System design guidance." },
  ],
  mcp_servers: [
    { id: "filesystem", name: "Filesystem MCP" },
  ],
};

function projectDetail(
  settings: CollaborationExtensionSettings,
  available = initialAvailable,
): CollaborationProjectPayload {
  return {
    project: {
      id: "project-1",
      name: "Product launch",
      created_at_ms: 1,
      updated_at_ms: 1,
    },
    task_lists: [],
    tasks: [],
    extension_profile: {
      revision: 1,
      settings,
    },
    available,
    context_sources: [],
  };
}

describe("ExtensionsPanel", () => {
  it("keeps an untouched omitted scope unrestricted when saving another scope", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <ExtensionsPanel
        detail={projectDetail({})}
        busy={false}
        onSave={onSave}
      />,
    );

    expect(screen.getByRole("checkbox", { name: /Architecture/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Filesystem MCP" })).toBeChecked();
    expect(screen.getByRole("button", { name: "Save extensions" })).toBeDisabled();

    await user.click(screen.getByRole("checkbox", { name: /Architecture/ }));
    await user.click(screen.getByRole("button", { name: "Save extensions" }));

    await waitFor(() => expect(onSave).toHaveBeenCalledWith({ skills: [] }));
  });

  it("renders explicit empty selection arrays as no selected extensions", () => {
    render(
      <ExtensionsPanel
        detail={projectDetail({ skills: [], mcpServers: [] })}
        busy={false}
        onSave={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    expect(screen.getByRole("checkbox", { name: /Architecture/ })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Filesystem MCP" })).not.toBeChecked();
  });

  it("selects newly available extensions for unrestricted scopes without changing explicit scopes", () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    const { rerender } = render(
      <ExtensionsPanel
        detail={projectDetail({ mcpServers: ["filesystem"] })}
        busy={false}
        onSave={onSave}
      />,
    );

    rerender(
      <ExtensionsPanel
        detail={projectDetail(
          { mcpServers: ["filesystem"] },
          {
            skills: [
              ...initialAvailable.skills,
              { id: "planning", name: "Planning", description: "Task planning guidance." },
            ],
            mcp_servers: [
              ...initialAvailable.mcp_servers,
              { id: "remote", name: "Remote MCP" },
            ],
          },
        )}
        busy={false}
        onSave={onSave}
      />,
    );

    expect(screen.getByRole("checkbox", { name: /Architecture/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Planning/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Filesystem MCP" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Remote MCP" })).not.toBeChecked();
  });
});
