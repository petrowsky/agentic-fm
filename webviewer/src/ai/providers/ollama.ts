import type { AIProvider, AIMessage, AIProviderConfig, AIStreamEvent } from '../types';

export const ollamaProvider: AIProvider = {
  id: 'ollama',
  displayName: 'Ollama (local)',
  defaultModel: 'qwen2.5-coder:14b',
  models: [
    'qwen2.5-coder:14b',
    'qwen2.5-coder:7b',
    'qwen2.5-coder:32b',
    'codellama:34b',
    'codellama:13b',
    'codellama:7b',
    'llama3:70b',
    'llama3:8b',
    'deepseek-coder-v2:16b',
  ],
  requiresKey: false,

  async chat(
    messages: AIMessage[],
    config: AIProviderConfig,
    onEvent: (event: AIStreamEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> {
    const ollamaMessages = messages.map(m => ({
      role: m.role as string,
      content: m.content,
    }));

    const response = await fetch('http://localhost:11434/v1/chat/completions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: config.model || this.defaultModel,
        messages: ollamaMessages,
        stream: true,
      }),
      signal,
    });

    if (!response.ok) {
      const err = await response.text();
      onEvent({ type: 'error', error: `Ollama error ${response.status}: ${err}` });
      return;
    }

    const reader = response.body?.getReader();
    if (!reader) {
      onEvent({ type: 'error', error: 'No response body' });
      return;
    }

    const decoder = new TextDecoder();
    let buffer = '';

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6);
            if (data === '[DONE]') continue;
            try {
              const event = JSON.parse(data);
              const delta = event.choices?.[0]?.delta?.content;
              if (delta) {
                onEvent({ type: 'text', text: delta });
              }
            } catch {
              // Skip malformed events
            }
          }
        }
      }
    } finally {
      reader.releaseLock();
    }

    onEvent({ type: 'done' });
  },

  async validateKey(): Promise<boolean> {
    try {
      const response = await fetch('http://localhost:11434/api/tags');
      return response.ok;
    } catch {
      return false;
    }
  },
};
