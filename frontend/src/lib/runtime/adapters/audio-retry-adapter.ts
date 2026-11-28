import { retryAudio } from '../commands/audio-retry';

export function getAudioRetryHandler() {
  return retryAudio;
}
