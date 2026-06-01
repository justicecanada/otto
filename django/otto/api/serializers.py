from rest_framework import serializers


class UserActivitySummaryBucketSerializer(serializers.Serializer):
    label = serializers.CharField()
    distinct_users = serializers.IntegerField()
    activity_total = serializers.IntegerField()


class UserActivitySummarySerializer(serializers.Serializer):
    activity_type = serializers.CharField()
    interval = serializers.CharField()
    start_date = serializers.DateField(allow_null=True)
    end_date = serializers.DateField(allow_null=True)
    include_inactive = serializers.BooleanField()
    total_users = serializers.IntegerField()
    total_activity = serializers.IntegerField()
    buckets = UserActivitySummaryBucketSerializer(many=True)


class UserActivityUserRowSerializer(serializers.Serializer):
    upn = serializers.CharField()
    email = serializers.EmailField()
    oid = serializers.CharField(allow_null=True)
    date_joined = serializers.DateField(allow_null=True)
    last_login = serializers.DateTimeField(allow_null=True)
    is_active = serializers.BooleanField()
    first_activity_date = serializers.DateField(allow_null=True)
    last_activity_date = serializers.DateField(allow_null=True)
    active_days = serializers.IntegerField()
    activity_events = serializers.IntegerField()
    chat_messages = serializers.IntegerField()
    files_created = serializers.IntegerField()
    text_extractor_requests = serializers.IntegerField()
    laws_queries = serializers.IntegerField()
    input_tokens = serializers.IntegerField()
    cached_input_tokens = serializers.IntegerField()
    output_tokens = serializers.IntegerField()
    embedding_tokens = serializers.IntegerField()
    total_tokens = serializers.IntegerField()
    sign_in_at_least_once = serializers.BooleanField()
    signed_in_in_period = serializers.BooleanField()
    selected_activity_total = serializers.IntegerField()
    used_in_period = serializers.BooleanField()


class PaginatedUserActivityUserRowSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    results = UserActivityUserRowSerializer(many=True)
