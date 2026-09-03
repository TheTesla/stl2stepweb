import asyncio
import subprocess
import os
import resource
from pathlib import Path
from typing import Optional
import logging

from app.models import ConversionJob, JobStatus
from app.config import get_settings

logger = logging.getLogger(__name__)


class ConversionWorker:
    def __init__(self, session_manager):
        self.session_manager = session_manager
        self.settings = get_settings()
        self.running = False
        self._convert_script = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "convert.py"
        )

    async def start(self):
        self.running = True
        asyncio.create_task(self._worker_loop())
        asyncio.create_task(self._cleanup_loop())

    async def stop(self):
        self.running = False

    async def _worker_loop(self):
        while self.running:
            job = self.session_manager.get_next_queued_job()
            if job:
                await self._process_job(job)
            else:
                await asyncio.sleep(1)

    async def _cleanup_loop(self):
        while self.running:
            await asyncio.sleep(self.settings.session.cleanup_interval_seconds)
            self.session_manager.cleanup_expired_sessions()

    async def _process_job(self, job: ConversionJob):
        if not self.session_manager.start_job(job.id):
            return

        try:
            output_path = await self._convert_stl_to_step(job)
            self.session_manager.complete_job(job.id, output_path)
            logger.info(f"Job {job.id} completed successfully")
        except asyncio.TimeoutError:
            self.session_manager.fail_job(job.id, "Conversion timeout exceeded")
            logger.error(f"Job {job.id} timed out")
        except MemoryError:
            self.session_manager.fail_job(job.id, "Memory limit exceeded")
            logger.error(f"Job {job.id} exceeded memory limit")
        except Exception as e:
            self.session_manager.fail_job(job.id, str(e))
            logger.error(f"Job {job.id} failed: {e}")

    async def _convert_stl_to_step(self, job: ConversionJob) -> str:
        output_dir = Path(self.settings.storage.temp_dir) / job.session_id
        output_dir.mkdir(parents=True, exist_ok=True)

        output_filename = f"{Path(job.original_filename).stem}.step"
        output_path = output_dir / output_filename

        proc = await asyncio.create_subprocess_exec(
            "python3", self._convert_script,
            job.upload_path,
            str(output_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            preexec_fn=self._set_resource_limits,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.settings.conversion.timeout_seconds
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise

        if proc.returncode != 0:
            error_msg = stderr.decode() if stderr else "Unknown error"
            raise RuntimeError(f"Conversion failed: {error_msg}")

        if not output_path.exists():
            raise RuntimeError("Output file was not created")

        return str(output_path)

    def _set_resource_limits(self):
        mem_limit = self.settings.conversion.memory_limit_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem_limit, mem_limit))
        resource.setrlimit(resource.RLIMIT_CPU, (self.settings.conversion.timeout_seconds, self.settings.conversion.timeout_seconds))