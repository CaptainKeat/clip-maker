import sys
from clipmaker.desktop import main

if __name__ == "__main__":
    try:
        if len(sys.argv)>2 and sys.argv[1]=='--speaker-worker':
            if sys.stderr is None:
                from pathlib import Path
                sys.stdout=sys.stderr=(Path(sys.argv[2]).parent/'worker.log').open('a',encoding='utf-8',buffering=1)
            from clipmaker.speaker_audio import worker
            worker(sys.argv[2])
        else: sys.exit(main())
    except Exception:
        import traceback
        traceback.print_exc()
        raise
