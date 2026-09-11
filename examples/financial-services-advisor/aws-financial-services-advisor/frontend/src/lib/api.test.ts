import { describe, expect, it, vi } from 'vitest'
import {
  decodeAgentEvent,
  splitSseFrames,
  streamChatMessage,
  type AgentEvent,
} from './api'

describe('splitSseFrames', () => {
  it('returns complete frames and keeps the partial remainder', () => {
    const { frames, rest } = splitSseFrames(
      'event: agent_start\ndata: {"agent":"supervisor"}\n\nevent: response\ndata: {"content"',
    )
    expect(frames).toEqual([{ event: 'agent_start', data: '{"agent":"supervisor"}' }])
    expect(rest).toBe('event: response\ndata: {"content"')
  })

  it('accepts CRLF line endings and multi-line data', () => {
    const { frames } = splitSseFrames('event: response\r\ndata: line1\r\ndata: line2\r\n\r\n')
    expect(frames).toEqual([{ event: 'response', data: 'line1\nline2' }])
  })

  it('skips frames with no event name', () => {
    const { frames } = splitSseFrames(': keep-alive\n\ndata: {"a":1}\n\n')
    expect(frames).toEqual([])
  })
})

describe('decodeAgentEvent', () => {
  it('tags the payload with its event type', () => {
    expect(
      decodeAgentEvent({ event: 'done', data: '{"session_id":"s1","tool_call_count":0}' }),
    ).toMatchObject({ type: 'done', session_id: 's1' })
  })

  it('warns instead of throwing on malformed JSON', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    expect(decodeAgentEvent({ event: 'done', data: '{nope' })).toBeNull()
    expect(warn).toHaveBeenCalled()
    warn.mockRestore()
  })

  it('ignores event types outside the protocol', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    expect(decodeAgentEvent({ event: 'surprise', data: '{}' })).toBeNull()
    warn.mockRestore()
  })
})

/** Build a Response whose body streams the given string pieces. */
function chunkedResponse(chunks: string[]): Response {
  const encoder = new TextEncoder()
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      controller.close()
    },
  })
  return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
}

describe('streamChatMessage', () => {
  it('delivers an event whose frame straddles a chunk boundary', async () => {
    // The split falls between the `event:` line and its `data:` line - the case
    // that used to silently drop the event.
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        chunkedResponse([
          'event: agent_start\n',
          'data: {"agent":"supervisor","timestamp":1}\n\nevent: response\ndata: {"content":"hi",',
          '"session_id":"s1"}\n\n',
        ]),
      )
    vi.stubGlobal('fetch', fetchMock)

    const events: AgentEvent[] = []
    await streamChatMessage({ message: 'hello' }, (event) => events.push(event))

    expect(events).toEqual([
      { type: 'agent_start', agent: 'supervisor', timestamp: 1 },
      { type: 'response', content: 'hi', session_id: 's1' },
    ])
    vi.unstubAllGlobals()
  })

  it('delivers a trailing frame that has no blank line', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>().mockResolvedValue(chunkedResponse(['event: done\ndata: {"session_id":"s2"}'])),
    )

    const events: AgentEvent[] = []
    await streamChatMessage({ message: 'hello' }, (event) => events.push(event))

    expect(events).toMatchObject([{ type: 'done', session_id: 's2' }])
    vi.unstubAllGlobals()
  })

  it('passes the abort signal through to fetch', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(chunkedResponse(['event: done\ndata: {}\n\n']))
    vi.stubGlobal('fetch', fetchMock)

    const controller = new AbortController()
    await streamChatMessage({ message: 'hello' }, () => undefined, {
      signal: controller.signal,
    })

    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({ signal: controller.signal })
    vi.unstubAllGlobals()
  })

  it('throws on a non-OK response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>().mockResolvedValue(new Response('nope', { status: 503 })),
    )
    await expect(streamChatMessage({ message: 'hello' }, () => undefined)).rejects.toThrow(
      /503/,
    )
    vi.unstubAllGlobals()
  })
})
