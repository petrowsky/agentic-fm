import type { AIProvider } from '../types';
import { anthropicProvider } from './anthropic';
import { openaiProvider } from './openai';
import { claudeCodeProvider } from './claude-code';
import { ollamaProvider } from './ollama';

const providers = new Map<string, AIProvider>();

// Register built-in providers
providers.set(anthropicProvider.id, anthropicProvider);
providers.set(openaiProvider.id, openaiProvider);
providers.set(claudeCodeProvider.id, claudeCodeProvider);
providers.set(ollamaProvider.id, ollamaProvider);

export function getProvider(id: string): AIProvider | undefined {
  return providers.get(id);
}

export function listProviders(): AIProvider[] {
  return Array.from(providers.values());
}

export function getDefaultProvider(): AIProvider {
  return anthropicProvider;
}
