# SecureModel Implementation Guide

The `SecureModel` provides a framework for implementing row-level security in Django models, ensuring that data access and modification are controlled based on permissions granted to users. This guide outlines how to utilize `SecureModel` effectively to enforce security measures in your Django application.

## Table of Contents
1. [Introduction](#introduction)
2. [Setup](#setup)
3. [Granting Permissions](#granting-permissions)
4. [Creating Objects](#creating-objects)
5. [Accessing Objects](#accessing-objects)
6. [Bypassing Security](#bypassing-security)
7. [Read-only Models](#read-only-models)

## Introduction <a name="introduction"></a>
`SecureModel` extends Django's model functionalities to incorporate fine-grained access control. It integrates with Django's authentication system and provides methods to grant, revoke, and check permissions for specific actions on model instances.

## Setup <a name="setup"></a>
To use `SecureModel`, ensure your model inherits from `SecureModel`.
   
   ```python
   from otto.secure_models import SecureModel

   class MyModel(SecureModel):
       # Define your model fields
   ```

## Granting Permissions <a name="granting-permissions"></a>
Granting permissions is crucial for controlling access to model instances. Use the following methods to grant permissions:

- `grant_create_to(AccessKey(user=request.user))`: Grant permission to create instances of the model. (Class method)
- `grant_change_to(AccessKey(user=request.user))`: Grant permission to modify instances of the model. (Instance method)
- `grant_delete_to(AccessKey(user=request.user))`: Grant permission to delete instances of the model. (Instance method)
- `grant_view_to(AccessKey(user=request.user))`: Grant permission to view instances of the model. (Instance method)

Example:
```python
from django.contrib.auth.models import User
from myapp.models import MyModel
from otto.secure_models import AccessKey

# Grant create permission to a user
user = User.objects.get(username='example_user')
access_key = AccessKey(user=user)
MyModel.grant_create_to(access_key)

# Grant view permission to a user for a specific object
my_object = MyModel.objects.get(pk=1)
my_object.grant_view_to(access_key)
```

If a user needs view, change, and delete, you can use `grant_ownership_to()` for convenience.

## Creating Objects <a name="creating-objects"></a>
When creating objects, ensure proper permissions are checked and logged. Use `save()` method with an `AccessKey` instance to enforce security.

Example:
```python
from django.contrib.auth.models import User
from myapp.models import MyModel
from otto.secure_models import AccessKey

# Assume `request.user` is the current user
user = request.user
access_key = AccessKey(user=user)

# Create an object with enforced security
obj = MyModel.objects.create(access_key=access_key)
```

## Accessing Objects <a name="accessing-objects"></a>
Accessing objects should always be performed in a secure manner. Use methods provided by `SecureManager` to filter objects based on user permissions.

Example:
```python
from django.contrib.auth.models import User
from myapp.models import MyModel
from otto.secure_models import AccessKey

# Assume `request.user` is the current user
user = request.user
access_key = AccessKey(user=user)

# Filter objects based on user permissions
objects = MyModel.objects.all(access_key=access_key)
for obj in objects:
    # Perform actions on objects
```

## Bypassing Security <a name="bypassing-security"></a>
In some scenarios, it may be necessary to bypass row-level security. This can be achieved by creating an `AccessKey` with the `bypass` flag set to `True`.

Example:
```python
from myapp.models import MyModel
from otto.secure_models import AccessKey

# Create an access key with bypass enabled
access_key = AccessKey(bypass=True)

# Access object without enforcing security
obj = MyModel.objects.get(access_key=access_key, id=object_id)
```

## Read-only Models <a name="read-only-models"></a>
If a model should be read-only, inherit from `SecureReadOnlyModel`. This ensures that attempts to modify the model result in an error unless explicitly bypassed.

Example:
```python
from otto.secure_models import SecureReadOnlyModel

class MyReadOnlyModel(SecureReadOnlyModel):
    # Define your model fields
```

## Conclusion
By following the guidelines outlined in this document, you can effectively implement row-level security in your Django application using `SecureModel`. Ensure that permissions are granted and enforced appropriately to maintain data integrity and security.
