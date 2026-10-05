import { afterEach, describe, expect, test } from "bun:test"
import { TextareaRenderable, TextRenderable } from "@opentui/core"
import { MockTreeSitterClient, createTestRenderer, type TestRendererSetup } from "@opentui/core/testing"
import { NanobotTui } from "./app"
import { options, client, waitUntil } from "./test-support"
import { Transcript } from "../rendering/transcript"

test("projects image media as stable placeholders without exposing filenames", async () => {
  const setup = await createTestRenderer({ width: 96, height: 24, screenMode: "alternate-screen" })
  const app = NanobotTui.mount(
    setup.renderer, options, client(), new MockTreeSitterClient({ autoResolveTimeout: 0 }),
  )
  try {
    // The application retains this renderer privately; observe its public rendered output.
    const ui = app as unknown as { transcript: Transcript }
    const transcript = ui.transcript
    transcript.user("What is this?", undefined, [
      { name: "clipboard-image-2.png" },
      { kind: "image", name: "screenshot.png" },
      { kind: "file", name: "report.pdf" },
    ])
    await setup.renderOnce()
    const frame = setup.captureCharFrame()
    expect(frame).toContain("What is this? [Image #2] [Image #1]")
    expect(frame).toContain("Attachments: report.pdf")
    expect(frame).not.toContain("clipboard-image-2.png")
    expect(frame).not.toContain("screenshot.png")
    transcript.user("Already labelled", undefined, [
      { name: "clipboard-image-1.png" },
    ], "Already labelled [Image #1]")
    await setup.renderOnce()
    const labelledFrame = setup.captureCharFrame()
    expect(labelledFrame).toContain("Already labelled [Image #1]")
    expect(labelledFrame).not.toContain("Already labelled [Image #1] [Image #1]")
  } finally {
    setup.renderer.destroy()
  }
})

describe("Mikobot title session navigation", () => {
  let setup: TestRendererSetup | undefined
  afterEach(() => {
    if (setup && !setup.renderer.isDestroyed) setup.renderer.destroy()
    setup = undefined
  })
  const createRenderer = createTestRenderer
  test("opens and switches sessions from the clickable title", async () => {
    const original = globalThis.fetch
    globalThis.fetch = ((input: string | URL | Request) => {
      const url = String(input)
      if (url.endsWith("/api/webui/sidebar-state")) {
        return Promise.resolve(new Response(JSON.stringify({})))
      }
      return Promise.resolve(new Response(JSON.stringify({
        sessions: [
          { key: "websocket:chat", title: "Current chat", preview: "Current work" },
          { key: "websocket:other", title: "Release checklist", preview: "Ship it" },
        ],
      })))
    }) as typeof fetch
    const attached: string[] = []
    setup = await createRenderer({ width: 96, height: 24, screenMode: "alternate-screen" })
    const app = NanobotTui.mount(
      setup.renderer,
      { ...options, apiUrl: "http://nanobot.test", apiToken: "secret" },
      client([], attached),
      new MockTreeSitterClient({ autoResolveTimeout: 0 }),
    )
    app.accept({ event: "attached", chat_id: "chat" })
    const ui = app as unknown as {
      composer: TextareaRenderable
      sessionMenu: { visible: boolean; root: { getChildren(): unknown[] } }
      titleText: TextRenderable
      status: TextRenderable
      ready: boolean
    }

    try {
      await waitUntil(() => ui.ready)
      await setup.renderOnce()
      await setup.mockMouse.click(ui.titleText.x + 2, ui.titleText.y)
      await waitUntil(() => ui.sessionMenu.visible)
      await setup.flush()
      expect(ui.composer.placeholder).toBe("Search sessions")

      const rows = ui.sessionMenu.root.getChildren() as TextRenderable[]
      const other = rows.find((row) => row.plainText.includes("Release checklist"))
      if (!other) throw new Error("other session row was not rendered")
      ui.composer.blur()
      await setup.mockMouse.click(other.x + 2, other.y)
      await waitUntil(() => attached.length === 1)
      expect(attached).toEqual(["other"])
      expect(ui.sessionMenu.visible).toBe(false)
      expect(ui.composer.focused).toBe(true)
      expect(ui.titleText.plainText).toContain("Release checklist")

      app.accept({ event: "attached", chat_id: "other" })
      await setup.mockMouse.click(ui.titleText.x + 2, ui.titleText.y)
      await waitUntil(() => ui.sessionMenu.visible)
      ui.composer.blur()
      await setup.mockMouse.click(ui.status.x, ui.status.y)
      expect(ui.sessionMenu.visible).toBe(false)
      expect(ui.composer.focused).toBe(true)
    } finally {
      globalThis.fetch = original
    }
  })
})
