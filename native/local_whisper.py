#!/usr/bin/env python3
"""Fixed local clip recognizer. Uses an already cached medium model only."""
import json
import os
import sys

def main():
    from faster_whisper import WhisperModel
    model = WhisperModel('medium', device='cpu', compute_type='int8',
                         cpu_threads=min(8, os.cpu_count() or 4), local_files_only=True)
    segments, _ = model.transcribe(sys.argv[1], language='zh', beam_size=5,
                                  condition_on_previous_text=False, vad_filter=True)
    text = ''.join(segment.text for segment in segments)
    if not text.strip():
        raise ValueError('本地识别未得到讲话内容。')
    print(json.dumps({'text': text, 'model': 'faster-whisper/medium/int8'}, ensure_ascii=False))

if __name__ == '__main__':
    main()
