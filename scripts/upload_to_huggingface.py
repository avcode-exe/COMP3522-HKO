import argparse
from pathlib import Path

from common import PROCESSED_ROOT, ensure_directories, load_env_file, now_hkt, setup_logger

DEFAULT_HF_REPO = "NotASI/COMP3522-HKO"


def push_processed_to_hf(repo_id=DEFAULT_HF_REPO, token=None, processed_root=None, private=False, logger=None):
    logger = logger or setup_logger()
    load_env_file()
    processed_root = Path(processed_root) if processed_root else PROCESSED_ROOT
    try:
        from huggingface_hub import HfApi
    except ImportError as exc:
        raise RuntimeError(
            "huggingface_hub is not installed. Install it with: "
            ".venv\\Scripts\\python.exe -m pip install huggingface_hub"
        ) from exc

    if not processed_root.exists() or not any(processed_root.rglob("*")):
        logger.warning("no processed data found at %s; nothing to upload", processed_root)
        return None

    api = HfApi(token=token)
    api.create_repo(repo_id=repo_id, repo_type="dataset", private=private, exist_ok=True)
    logger.info("uploading %s -> https://huggingface.co/datasets/%s", processed_root, repo_id)
    api.upload_folder(
        repo_id=repo_id,
        repo_type="dataset",
        folder_path=str(processed_root),
        commit_message="Update COMP3522-HKO processed dataset ({})".format(now_hkt().isoformat(timespec="seconds")),
    )
    url = "https://huggingface.co/datasets/{}".format(repo_id)
    logger.info("upload complete: %s", url)
    return url


def main():
    ensure_directories()
    parser = argparse.ArgumentParser(description="Upload processed/ to a Hugging Face dataset repository (optional)")
    parser.add_argument("--repo-id", default=DEFAULT_HF_REPO, help="Hugging Face dataset repo id (default: {})".format(DEFAULT_HF_REPO))
    parser.add_argument("--token", default=None, help="HF access token (default: HF_TOKEN env var or cached huggingface-cli login)")
    parser.add_argument("--private", action="store_true", help="create the repo as private (default: public)")
    parser.add_argument("--path", default=None, help="folder to upload (default: processed/)")
    args = parser.parse_args()
    push_processed_to_hf(repo_id=args.repo_id, token=args.token, processed_root=args.path, private=args.private)


if __name__ == "__main__":
    main()
