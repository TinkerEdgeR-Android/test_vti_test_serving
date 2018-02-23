#!/usr/bin/env python
#
# Copyright (C) 2017 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import datetime
import webapp2

from webapp.src import vtslab_status as Status
from webapp.src.proto import model
from webapp.src.utils import logger


def StrGT(left, right):
    """Returns true if `left` string is greater than `right` in value."""
    if len(left) > len(right):
        right = "0" * (len(left) - len(right)) + right
    elif len(right) > len(left):
        left = "0" * (len(right) - len(left)) + left
    return left > right


class PeriodicScheduler(webapp2.RequestHandler):
    """Main class for /tasks/schedule servlet.

    This class creates jobs from registered schedules periodically.

    Attributes:
        logger: Logger class
    """
    logger = logger.Logger()

    def ReserveDevices(self, target_device_serials):
        """Reserves devices.

        Args:
            target_device_serials: a list of strings, containing target device
                                   serial numbers.
        """
        device_query = model.DeviceModel.query(
            model.DeviceModel.serial.IN(target_device_serials)
        )
        devices = device_query.fetch()
        for device in devices:
            device.scheduling_status = Status.DEVICE_SCHEDULING_STATUS_DICT[
                "reserved"]
            device.put()

    def FindBuildId(self, new_job):
        """Finds build ID for a new job.

        Args:
            new_job: JobModel, a new job.

        Return:
            string, build ID found.
        """
        build_id = ""
        build_query = model.BuildModel.query(
            model.BuildModel.manifest_branch == new_job.manifest_branch
        )
        builds = build_query.fetch()

        if builds:
            self.logger.Println("-- Find build ID")
            # Remove builds if build_id info is none
            build_id_filled = [x for x in builds if x.build_id]
            sorted_list = sorted(
                build_id_filled,
                key=lambda x: int(x.build_id),
                reverse=True)
            filtered_list = [
                x for x in sorted_list if (
                    all(
                        hasattr(x, attrs) for attrs in [
                            "build_target", "build_type", "build_id"
                        ])
                    and x.build_target and x.build_type)
            ]
            for device_build in filtered_list:
                candidate_build_target = "-".join(
                    [device_build.build_target, device_build.build_type])
                if new_job.build_target == candidate_build_target:
                    build_id = device_build.build_id
                    break
        return build_id

    def get(self):
        """Generates an HTML page based on the task schedules kept in DB."""
        self.logger.Clear()

        schedule_query = model.ScheduleModel.query()
        schedules = schedule_query.fetch()

        if schedules:
            for schedule in schedules:
                self.logger.Println("Schedule: %s (%s %s)" % (
                    schedule.test_name, schedule.manifest_branch,
                    schedule.build_target))
                self.logger.Indent()
                if self.NewPeriod(schedule):
                    self.logger.Println("- Need new job")
                    target_host, target_device_serials = self.SelectTargetLab(
                        schedule)
                    self.logger.Println("- Target host: %s" % target_host)
                    self.logger.Println(
                        "- Target serials: %s" % target_device_serials)
                    # TODO: update device status

                    # create job and add.
                    if target_host:
                        new_job = model.JobModel()
                        new_job.hostname = target_host
                        new_job.priority = schedule.priority
                        new_job.test_name = schedule.test_name
                        new_job.device = schedule.device
                        new_job.period = schedule.period
                        new_job.serial.extend(target_device_serials)
                        new_job.manifest_branch = schedule.manifest_branch
                        new_job.build_target = schedule.build_target
                        new_job.shards = schedule.shards
                        new_job.param = schedule.param
                        new_job.gsi_branch = schedule.gsi_branch
                        new_job.gsi_build_target = schedule.gsi_build_target
                        new_job.gsi_pab_account_id = schedule.gsi_pab_account_id
                        new_job.test_branch = schedule.test_branch
                        new_job.test_build_target = schedule.test_build_target
                        new_job.test_pab_account_id = \
                            schedule.test_pab_account_id

                        # assume device build
                        #_, device_builds, _ = build_list.ReadBuildInfo()

                        new_job.build_id = ""
                        new_job.build_id = self.FindBuildId(new_job)

                        if new_job.build_id:
                            self.ReserveDevices(target_device_serials)
                            # TODO remove only until full builds are available.
                            new_job.status = Status.JOB_STATUS_DICT["ready"]
                            new_job.timestamp = datetime.datetime.now()
                            new_job.put()
                            self.logger.Println("NEW JOB")
                        else:
                            self.logger.Println("NO BUILD FOUND")
                self.logger.Unindent()

        self.response.write(
            "<pre>\n" + "\n".join(self.logger.Get()) + "\n</pre>")

    def NewPeriod(self, schedule):
        """Checks whether a new job creation is needed.

        Args:
            schedule: a proto containing schedule information.

        Returns:
            True if new job is required, False otherwise.
        """
        job_query = model.JobModel.query(
            model.JobModel.manifest_branch == schedule.manifest_branch,
            model.JobModel.build_target == schedule.build_target,
            model.JobModel.test_name == schedule.test_name,
            model.JobModel.period == schedule.period,
            model.JobModel.device == schedule.device,
            model.JobModel.shards == schedule.shards,
            model.JobModel.gsi_branch == schedule.gsi_branch,
            model.JobModel.test_branch == schedule.test_branch
        )
        same_jobs = job_query.fetch()
        same_jobs = [x for x in same_jobs
                     if set(x.param) == set(schedule.param)]
        if not same_jobs:
            return True

        ready_jobs = [x for x in same_jobs
                      if x.status == Status.JOB_STATUS_DICT["ready"]]
        if ready_jobs:
            return False

        same_jobs = sorted(
            same_jobs, key=lambda x: x.timestamp, reverse=True)
        if (same_jobs[0].timestamp <=
            (datetime.datetime.now() -
             datetime.timedelta(minutes=same_jobs[0].period))):
            return True
        else:
            return False

    def SelectTargetLab(self, schedule):
        """Find target host and devices to schedule a new job.

        Args:
            schedule: a proto containing the information of a schedule.

        Returns:
            hostname,
            a list of selected devices  (see whether devices will be selected
            later when the job is picked up.)
        """
        if "/" not in schedule.device:
            # device malformed
            return None, None

        target_lab, target_product_type = schedule.device.split("/")
        self.logger.Println("- Seeking product %s in lab %s" %
                        (target_product_type, target_lab))
        self.logger.Indent()
        lab_query = model.LabModel.query(
            model.LabModel.name == target_lab
        )
        target_labs = lab_query.fetch()

        available_devices = {}
        if target_labs:
            for lab in target_labs:
                self.logger.Println("- target lab found")
                self.logger.Println("- target device %s %s" %
                                (lab.hostname, target_product_type))
                self.logger.Indent()
                device_query = model.DeviceModel.query(
                    model.DeviceModel.hostname == lab.hostname
                )
                host_devices = device_query.fetch()

                for device in host_devices:
                    self.logger.Println("- check device %s %s" %
                                    (device.status, device.product))
                    if ((device.status in [
                            Status.DEVICE_STATUS_DICT["fastboot"],
                            Status.DEVICE_STATUS_DICT["online"],
                            Status.DEVICE_STATUS_DICT["ready"]
                    ]) and (device.scheduling_status ==
                            Status.DEVICE_SCHEDULING_STATUS_DICT["free"]
                    ) and device.product == target_product_type):
                        self.logger.Println(
                            "- a device found %s" % device.serial)
                        if device.hostname not in available_devices:
                            available_devices[device.hostname] = set()
                        available_devices[device.hostname].add(device.serial)
                self.logger.Unindent()
            for host in available_devices:
                self.logger.Println("- len(devices) %s >= shards %s ?" %
                                (len(available_devices[host]), schedule.shards))
                if len(available_devices[host]) >= schedule.shards:
                    self.logger.Unindent()
                    return host, list(available_devices[host])[:schedule.shards]
        self.logger.Unindent()
        return None, []
