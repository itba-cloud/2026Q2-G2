# Every new object in raw/ (a chain's file from the ingest, a merchant's CSV
# later) becomes a message in the jobs queue: the only way a job is created.
# It lives here, not in either module, because it joins the bucket of one
# with the queue of the other.
resource "aws_s3_bucket_notification" "raw" {
  bucket = module.data_lake.bucket_name

  queue {
    id            = "raw-to-jobs"
    queue_arn     = module.jobs_queue.queue_arn
    events        = ["s3:ObjectCreated:*"]
    filter_prefix = "raw/"
  }

  # S3 checks it may send to the queue when the notification is created, and
  # nothing in the arguments above refers to the queue's policy: without
  # this, the apply can fail with "Unable to validate the destination". The
  # whole module, since a resource inside it cannot be named from here.
  depends_on = [module.jobs_queue]
}
