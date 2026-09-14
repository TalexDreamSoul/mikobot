import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ProjectTasksPanel } from "@/components/projects/ProjectTasksPanel";
import { ProjectsView } from "@/components/projects/ProjectsView";
import i18n from "@/i18n";
import {
  controller,
  detail,
  doneTask,
  openTask,
  wrap,
} from "@/tests/collaboration-fixtures";

afterEach(async () => {
  cleanup();
  vi.clearAllMocks();
  vi.useRealTimers();
  await i18n.changeLanguage("en");
});

describe("ProjectTasksPanel", () => {
  it("groups the project's cards into columns and marks who added them", () => {
    render(
      <ProjectTasksPanel
        detail={detail({
          tasks: [
            openTask,
            doneTask,
            {
              ...openTask,
              id: "task-3",
              title: "Legacy card",
              detail: "",
              created_by_user_id: "user-gone",
            },
          ],
        })}
        currentUserId="user-1"
        busyKey={null}
        onCreate={vi.fn()}
        onUpdate={vi.fn()}
        onDelete={vi.fn()}
      />,
    );

    const todo = screen.getByRole("heading", { name: /To do/ }).closest("div")!;
    const done = screen.getByRole("heading", { name: /Done/ }).closest("div")!;
    expect(within(todo).getByText(openTask.title)).toBeTruthy();
    expect(within(todo).getByText("Added by user-2")).toBeTruthy();
    expect(within(todo).getByText("Cover the pricing change.")).toBeTruthy();
    expect(within(todo).getByText("Added by another member")).toBeTruthy();
    expect(within(done).getByText(doneTask.title)).toBeTruthy();
    expect(within(done).getByText("Added by you")).toBeTruthy();

    // The board cannot move a card past its ends.
    expect(within(todo).getByRole("button", {
      name: `Move “${openTask.title}” toward To do`,
    })).toBeDisabled();
    expect(within(todo).getByRole("button", {
      name: `Move “${openTask.title}” toward Done`,
    })).toBeEnabled();
    expect(within(done).getByRole("button", {
      name: `Move “${doneTask.title}” toward Done`,
    })).toBeDisabled();
    expect(within(done).getByRole("button", {
      name: `Move “${doneTask.title}” toward To do`,
    })).toBeEnabled();
  });

  it("moves a card to the next column", async () => {
    const onUpdate = vi.fn().mockResolvedValue(undefined);
    render(
      <ProjectTasksPanel
        detail={detail()}
        currentUserId="user-1"
        busyKey={null}
        onCreate={vi.fn()}
        onUpdate={onUpdate}
        onDelete={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", {
      name: `Move “${openTask.title}” toward Done`,
    }));
    await waitFor(() => expect(onUpdate).toHaveBeenCalledWith(openTask.id, { status: "doing" }));

    fireEvent.click(screen.getByRole("button", {
      name: `Move “${doneTask.title}” toward To do`,
    }));
    await waitFor(() => expect(onUpdate).toHaveBeenLastCalledWith(doneTask.id, { status: "doing" }));
  });

  it("creates a card with its note and edits an existing one in place", async () => {
    const onCreate = vi.fn().mockResolvedValue(undefined);
    const onUpdate = vi.fn().mockResolvedValue(undefined);
    render(
      <ProjectTasksPanel
        detail={detail()}
        currentUserId="user-1"
        busyKey={null}
        onCreate={onCreate}
        onUpdate={onUpdate}
        onDelete={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "New task" }));
    const form = screen.getByRole("dialog");
    expect(within(form).getByRole("button", { name: "New task" })).toBeDisabled();
    fireEvent.change(within(form).getByLabelText("Title"), { target: { value: "Book the venue" } });
    fireEvent.change(within(form).getByLabelText("Note"), { target: { value: "Week 34" } });
    fireEvent.click(within(form).getByRole("button", { name: "New task" }));
    await waitFor(() => expect(onCreate).toHaveBeenCalledWith("Book the venue", "Week 34"));
    expect(onUpdate).not.toHaveBeenCalled();

    const card = screen.getByText(openTask.title).closest("li")!;
    fireEvent.click(within(card).getByRole("button", { name: `Edit ${openTask.title}` }));
    const editForm = screen.getByRole("dialog");
    expect(within(editForm).getByLabelText("Title")).toHaveValue(openTask.title);
    fireEvent.change(within(editForm).getByLabelText("Title"), { target: { value: "Retitled card" } });
    fireEvent.change(within(editForm).getByLabelText("Note"), { target: { value: "" } });
    fireEvent.click(within(editForm).getByRole("button", { name: "Save task" }));
    await waitFor(() => expect(onUpdate).toHaveBeenCalledWith(openTask.id, {
      title: "Retitled card",
      detail: "",
    }));
  });

  it("discards a card only after confirmation", async () => {
    const onDelete = vi.fn().mockResolvedValue(undefined);
    render(
      <ProjectTasksPanel
        detail={detail()}
        currentUserId="user-1"
        busyKey={null}
        onCreate={vi.fn()}
        onUpdate={vi.fn()}
        onDelete={onDelete}
      />,
    );

    const card = screen.getByText(openTask.title).closest("li")!;
    fireEvent.click(within(card).getByRole("button", { name: `Delete ${openTask.title}` }));
    expect(onDelete).not.toHaveBeenCalled();
    expect(screen.getByRole("alertdialog")).toBeTruthy();
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", {
      name: "Delete task",
    }));
    await waitFor(() => expect(onDelete).toHaveBeenCalledWith(openTask.id));
  });

  it("keeps a failed edit on screen so the author can retry", async () => {
    const onUpdate = vi.fn().mockRejectedValue(new Error("revision conflict"));
    render(
      <ProjectTasksPanel
        detail={detail()}
        currentUserId="user-1"
        busyKey={null}
        onCreate={vi.fn()}
        onUpdate={onUpdate}
        onDelete={vi.fn()}
      />,
    );

    const card = screen.getByText(openTask.title).closest("li")!;
    fireEvent.click(within(card).getByRole("button", { name: `Edit ${openTask.title}` }));
    const form = screen.getByRole("dialog");
    fireEvent.change(within(form).getByLabelText("Title"), { target: { value: "Retry me" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save task" }));

    await waitFor(() => expect(onUpdate).toHaveBeenCalled());
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(within(screen.getByRole("dialog")).getByLabelText("Title")).toHaveValue("Retry me");
  });
});

describe("ProjectsView task board", () => {
  it("opens on the board and adds it to the project sections", () => {
    render(wrap(<ProjectsView projects={controller()} onToggleSidebar={vi.fn()} />));

    expect(screen.getByRole("heading", { name: "Task board" })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Tasks/ })).toBeTruthy();
    expect(screen.getByText(openTask.title)).toBeTruthy();
  });
});
