# [OPEN] gpu-not-used

## Symptoms
- Audio transcription does not show obvious GPU or CPU usage in Windows Task Manager.
- Current `celery-worker` reports no visible CUDA devices.

## Hypotheses
1. The running worker container was created before GPU compose changes and still has no GPU request.
2. Host Docker/WSL/NVIDIA integration does not support `--gpus all`, so containers cannot access the GPU.
3. Docker can expose the GPU, but the current container image/runtime still lacks effective CUDA visibility.
4. The workload is reaching CPU-only code paths despite the GPU-capable image.
5. The work is real but Windows Task Manager is not showing the load where expected.

## Evidence
- Host `nvidia-smi` succeeds and shows `NVIDIA GeForce RTX 4060 Ti`, so the Windows/NVIDIA driver layer is healthy.
- `docker info` shows `Runtimes: ... nvidia ...`, so Docker Desktop has NVIDIA runtime support.
- `docker run --rm --gpus all nvidia/cuda:12.4.0-runtime-ubuntu22.04 nvidia-smi` succeeds, proving Docker-to-GPU passthrough works on this machine.
- Before worker recreation, the running `tts-celery-worker` had `DeviceRequests = null` and `torch.cuda.is_available() = False`.
- After recreating `celery-worker` with the updated compose, `DeviceRequests` becomes `[{"Capabilities":[["gpu"]],...}]` and container-side torch reports `cuda_available=True`, `cuda_device_count=1`, `cuda_name=NVIDIA GeForce RTX 4060 Ti`.
- The ASR code creates FunASR `AutoModel(...)` instances without any explicit `device`/`cuda` selection.

## Conclusion
- Host GPU environment is healthy.
- Docker GPU passthrough is healthy.
- The previous issue was that the running worker had not been recreated with GPU settings.
- The current remaining risk is application-level: FunASR model initialization may still default to CPU because the code does not explicitly select CUDA.

## Next Step
- Decide whether to modify ASR initialization so it explicitly prefers CUDA when available, then verify with a fresh transcription run.
