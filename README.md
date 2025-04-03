# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/justicecanada/otto/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                                                     |    Stmts |     Miss |   Cover |   Missing |
|----------------------------------------------------------------------------------------- | -------: | -------: | ------: | --------: |
| django/cache\_tiktoken.py                                                                |        8 |        8 |      0% |      1-18 |
| django/chat/\_\_init\_\_.py                                                              |        0 |        0 |    100% |           |
| django/chat/admin.py                                                                     |        1 |        1 |      0% |         1 |
| django/chat/apps.py                                                                      |        4 |        0 |    100% |           |
| django/chat/forms.py                                                                     |      163 |       21 |     87% |42, 49, 106, 139-154, 162-176, 195, 240, 248, 419, 421-423, 503-505 |
| django/chat/llm.py                                                                       |      112 |       18 |     84% |75, 97-99, 105-107, 133-148, 253, 275 |
| django/chat/migrations/0001\_initial.py                                                  |        6 |        0 |    100% |           |
| django/chat/migrations/0002\_initial.py                                                  |        6 |        0 |    100% |           |
| django/chat/migrations/0003\_initial.py                                                  |        7 |        0 |    100% |           |
| django/chat/migrations/0004\_alter\_message\_options.py                                  |        4 |        0 |    100% |           |
| django/chat/migrations/0005\_answersource\_highlighted\_text\_message\_claims\_list.py   |        4 |        0 |    100% |           |
| django/chat/migrations/0006\_rename\_highlighted\_text\_answersource\_processed\_text.py |        4 |        0 |    100% |           |
| django/chat/migrations/0007\_auto\_20250303\_1724.py                                     |        7 |        0 |    100% |           |
| django/chat/migrations/0007\_chat\_last\_modification\_date.py                           |       10 |        2 |     80% |     10-11 |
| django/chat/migrations/0008\_merge\_20250310\_1421.py                                    |        4 |        0 |    100% |           |
| django/chat/migrations/\_\_init\_\_.py                                                   |        0 |        0 |    100% |           |
| django/chat/models.py                                                                    |      342 |       38 |     89% |33, 84, 222-225, 230-236, 244, 364, 381-382, 386-390, 397, 402, 408-409, 412, 441, 461, 479-483, 535, 539-541, 556, 567, 605, 615, 646-647 |
| django/chat/prompts.py                                                                   |        5 |        0 |    100% |           |
| django/chat/responses.py                                                                 |      306 |       87 |     72% |74, 112, 210, 264, 270-290, 358-359, 364-397, 400-430, 472, 478-488, 538, 584-618, 624-628, 687, 714, 718, 759-760 |
| django/chat/tasks.py                                                                     |       71 |       16 |     77% |22-30, 92-93, 96-101 |
| django/chat/templatetags/\_\_init\_\_.py                                                 |        0 |        0 |    100% |           |
| django/chat/templatetags/chat\_tags.py                                                   |        5 |        0 |    100% |           |
| django/chat/urls.py                                                                      |        6 |        0 |    100% |           |
| django/chat/utils.py                                                                     |      419 |       58 |     86% |126, 138-139, 151-155, 199, 218, 220-221, 233-249, 257-258, 265-266, 304-320, 352-354, 369-371, 393, 465, 467, 484, 538-545, 553, 570-574, 610-620, 627, 921-922 |
| django/chat/views.py                                                                     |      458 |       81 |     82% |83-91, 107-109, 147, 175-177, 180-182, 206, 223-230, 236, 337-341, 427, 447-467, 493-495, 525, 528, 594, 607, 642-643, 712-720, 752-764, 827-843, 853-854, 863-866, 904-913, 919-924 |
| django/import\_timer.py                                                                  |        6 |        6 |      0% |       1-8 |
| django/laws/\_\_init\_\_.py                                                              |        0 |        0 |    100% |           |
| django/laws/admin.py                                                                     |        1 |        1 |      0% |         1 |
| django/laws/apps.py                                                                      |        4 |        0 |    100% |           |
| django/laws/forms.py                                                                     |       54 |        6 |     89% |24-29, 38, 52-57, 66 |
| django/laws/management/commands/load\_laws\_xml.py                                       |      451 |      120 |     73% |28-57, 72, 83-85, 101-104, 114-118, 146, 175, 236, 254, 256, 258, 277, 280, 282, 297-298, 300-301, 398-401, 411-429, 455-459, 471, 497, 549-550, 591-593, 709-715, 733-734, 736, 744, 784, 786, 804-806, 845-847, 850-852, 879-881, 883-885, 887-889, 891-893, 946-948, 965-967, 985-991, 1039-1050, 1055, 1068-1069, 1094-1100 |
| django/laws/migrations/0001\_initial.py                                                  |        5 |        0 |    100% |           |
| django/laws/migrations/0002\_law\_laws\_law\_title\_797cd1\_idx\_and\_more.py            |        4 |        0 |    100% |           |
| django/laws/migrations/\_\_init\_\_.py                                                   |        0 |        0 |    100% |           |
| django/laws/models.py                                                                    |      105 |       22 |     79% |38-42, 86, 111-114, 148, 152-160, 164-165 |
| django/laws/prompts.py                                                                   |        2 |        0 |    100% |           |
| django/laws/translation.py                                                               |        5 |        0 |    100% |           |
| django/laws/urls.py                                                                      |        4 |        0 |    100% |           |
| django/laws/utils.py                                                                     |       71 |       11 |     85% |37, 62-67, 78, 94-96 |
| django/laws/views.py                                                                     |      216 |       29 |     87% |71, 75, 92, 105, 122, 152-159, 169, 204, 221, 243, 286, 288, 293-295, 307, 311, 337, 345, 353, 362, 366, 373-378, 441-449 |
| django/librarian/\_\_init\_\_.py                                                         |        0 |        0 |    100% |           |
| django/librarian/admin.py                                                                |        5 |        5 |      0% |       1-7 |
| django/librarian/apps.py                                                                 |        4 |        0 |    100% |           |
| django/librarian/forms.py                                                                |      101 |        5 |     95% |125-126, 211, 215, 229 |
| django/librarian/migrations/0001\_initial.py                                             |        7 |        0 |    100% |           |
| django/librarian/migrations/0002\_initial.py                                             |        7 |        0 |    100% |           |
| django/librarian/migrations/\_\_init\_\_.py                                              |        0 |        0 |    100% |           |
| django/librarian/models.py                                                               |      330 |       48 |     85% |53-55, 123, 125, 133, 135, 137, 147, 172-174, 192, 196, 250, 312-313, 318, 329-332, 405, 422-431, 435, 453, 481-483, 493-494, 500, 516, 542-543, 553-554, 564-565, 578-579 |
| django/librarian/tasks.py                                                                |      115 |       39 |     66% |42-75, 82, 96, 109, 119, 139, 161-163, 174-177, 196-197 |
| django/librarian/translation.py                                                          |        8 |        0 |    100% |           |
| django/librarian/urls.py                                                                 |        4 |        0 |    100% |           |
| django/librarian/utils/markdown\_splitter.py                                             |      183 |       10 |     95% |72, 75-77, 88, 123, 137, 260, 270, 277 |
| django/librarian/utils/process\_engine.py                                                |      462 |       55 |     88% |45-47, 52, 148, 153, 163-164, 168, 174, 177, 184, 186, 188, 190, 196, 198, 200, 248, 261, 273-274, 287-296, 298-300, 350-356, 402, 426, 442-444, 493-497, 503-507, 511, 559-560, 594 |
| django/librarian/views.py                                                                |      309 |       42 |     86% |71-92, 98, 126-145, 178, 238-239, 244, 280, 312-313, 332, 339-341, 459, 464 |
| django/manage.py                                                                         |       11 |       11 |      0% |      3-23 |
| django/otto/\_\_init\_\_.py                                                              |        2 |        0 |    100% |           |
| django/otto/admin.py                                                                     |        0 |        0 |    100% |           |
| django/otto/asgi.py                                                                      |        8 |        8 |      0% |     10-24 |
| django/otto/celery.py                                                                    |       16 |        1 |     94% |        78 |
| django/otto/context\_processors.py                                                       |       11 |        4 |     64% |     10-14 |
| django/otto/forms.py                                                                     |       76 |        4 |     95% |73, 75, 215-216 |
| django/otto/management/commands/delete\_empty\_chats.py                                  |       19 |        1 |     95% |        29 |
| django/otto/management/commands/delete\_old\_chats.py                                    |       21 |        2 |     90% |    32, 36 |
| django/otto/management/commands/delete\_text\_extractor\_files.py                        |       18 |        0 |    100% |           |
| django/otto/management/commands/delete\_translation\_files.py                            |       27 |        0 |    100% |           |
| django/otto/management/commands/delete\_unused\_libraries.py                             |       21 |        2 |     90% |    32, 36 |
| django/otto/management/commands/reset\_app\_data.py                                      |      122 |       18 |     85% |70-75, 90, 107-112, 132-137, 151-152, 157-160, 175-180, 191 |
| django/otto/management/commands/test\_laws\_query.py                                     |       52 |       38 |     27% |18-121, 128-135 |
| django/otto/management/commands/update\_exchange\_rate.py                                |       19 |        0 |    100% |           |
| django/otto/management/commands/warn\_libraries\_pending\_deletion.py                    |       26 |        3 |     88% |     29-33 |
| django/otto/migrations/0001\_initial.py                                                  |        8 |        0 |    100% |           |
| django/otto/migrations/0002\_user\_ai\_assistant\_tour\_completed\_and\_more.py          |        4 |        0 |    100% |           |
| django/otto/migrations/0002\_visitor.py                                                  |        6 |        0 |    100% |           |
| django/otto/migrations/0003\_merge\_20250401\_2012.py                                    |        4 |        0 |    100% |           |
| django/otto/migrations/\_\_init\_\_.py                                                   |        0 |        0 |    100% |           |
| django/otto/models.py                                                                    |      287 |       30 |     90% |28-30, 84-87, 120, 124-127, 162, 208, 211, 227, 248, 266, 383, 386, 440, 447, 475, 479, 486, 492, 541-542, 556, 560, 564, 586 |
| django/otto/rules.py                                                                     |      157 |       18 |     89% |26, 41, 48, 50, 100-102, 107-109, 114-116, 145, 211-213, 249 |
| django/otto/secure\_models.py                                                            |      248 |       91 |     63% |21-22, 61, 86-100, 129-130, 135-136, 149-154, 183-224, 248, 268-269, 307, 337, 350, 359, 378, 393, 398, 403, 409-415, 418, 423, 437, 442, 447, 454-482, 485-486, 491-498, 501-502, 508-522, 536-537, 542-552, 557-558, 561-562 |
| django/otto/settings.py                                                                  |      160 |       23 |     86% |38-41, 51-52, 218-227, 297, 310, 367-374, 406, 496-497 |
| django/otto/tasks.py                                                                     |       42 |        7 |     83% |10, 15, 39, 59, 72-74 |
| django/otto/templatetags/\_\_init\_\_.py                                                 |        0 |        0 |    100% |           |
| django/otto/templatetags/filters.py                                                      |       10 |        1 |     90% |         8 |
| django/otto/templatetags/tags.py                                                         |       10 |        1 |     90% |        18 |
| django/otto/translation.py                                                               |       17 |        0 |    100% |           |
| django/otto/urls.py                                                                      |       13 |        2 |     85% |  106, 111 |
| django/otto/utils/auth.py                                                                |       37 |        9 |     76% |14-28, 66-68 |
| django/otto/utils/common.py                                                              |       57 |        1 |     98% |        94 |
| django/otto/utils/decorators.py                                                          |       62 |        4 |     94% |24-25, 65, 87 |
| django/otto/utils/logging.py                                                             |       15 |        0 |    100% |           |
| django/otto/utils/middleware.py                                                          |       41 |        1 |     98% |        31 |
| django/otto/views.py                                                                     |      571 |      130 |     77% |59, 64, 69-83, 125, 134-144, 156, 281, 381, 433-436, 452-453, 477, 487-490, 519-529, 541-546, 549, 558, 560-563, 565-566, 568-571, 594, 602, 611, 627-638, 744-745, 776, 778, 780, 794, 796, 803-804, 807-810, 820-826, 836, 838, 840, 845-865, 904, 913-922, 1001, 1008-1014, 1037-1038, 1058, 1089, 1122-1145, 1169-1174, 1182-1185 |
| django/otto/wsgi.py                                                                      |        4 |        4 |      0% |     10-16 |
| django/postgres\_wrapper/\_\_init\_\_.py                                                 |        0 |        0 |    100% |           |
| django/postgres\_wrapper/base.py                                                         |        6 |        0 |    100% |           |
| django/tests/\_\_init\_\_.py                                                             |        0 |        0 |    100% |           |
| django/tests/chat/test\_answer\_sources.py                                               |       38 |        0 |    100% |           |
| django/tests/chat/test\_chat\_models.py                                                  |       36 |        1 |     97% |        48 |
| django/tests/chat/test\_chat\_options.py                                                 |      119 |        2 |     98% |   175-176 |
| django/tests/chat/test\_chat\_procs.py                                                   |      271 |        1 |     99% |        48 |
| django/tests/chat/test\_chat\_readonly.py                                                |       33 |        0 |    100% |           |
| django/tests/chat/test\_chat\_translate.py                                               |       37 |        0 |    100% |           |
| django/tests/chat/test\_chat\_views.py                                                   |      648 |       12 |     98% |31, 581-599 |
| django/tests/chat/test\_highlights.py                                                    |       50 |        0 |    100% |           |
| django/tests/conftest.py                                                                 |      212 |        6 |     97% |40, 214, 244-248, 341 |
| django/tests/laws/conftest.py                                                            |        9 |        0 |    100% |           |
| django/tests/laws/test\_laws\_utils.py                                                   |       45 |        0 |    100% |           |
| django/tests/laws/test\_laws\_views.py                                                   |       48 |        0 |    100% |           |
| django/tests/librarian/test\_document\_loading.py                                        |      179 |        0 |    100% |           |
| django/tests/librarian/test\_file\_uploads.py                                            |       81 |        0 |    100% |           |
| django/tests/librarian/test\_librarian.py                                                |      277 |        0 |    100% |           |
| django/tests/librarian/test\_markdown\_splitter.py                                       |      282 |        0 |    100% |           |
| django/tests/otto/test\_budget.py                                                        |       37 |        0 |    100% |           |
| django/tests/otto/test\_cleanup.py                                                       |      306 |        0 |    100% |           |
| django/tests/otto/test\_commands\_delete\_translation\_files.py                          |       36 |        0 |    100% |           |
| django/tests/otto/test\_exchange\_rate\_update.py                                        |       11 |        0 |    100% |           |
| django/tests/otto/test\_feedback\_dashboard.py                                           |      109 |        0 |    100% |           |
| django/tests/otto/test\_load\_test.py                                                    |       64 |        0 |    100% |           |
| django/tests/otto/test\_manage\_users.py                                                 |      130 |        0 |    100% |           |
| django/tests/otto/test\_otto\_forms.py                                                   |       11 |        0 |    100% |           |
| django/tests/otto/test\_otto\_models.py                                                  |       37 |        0 |    100% |           |
| django/tests/otto/test\_otto\_views.py                                                   |       63 |        0 |    100% |           |
| django/tests/otto/test\_utils\_common.py                                                 |       13 |        0 |    100% |           |
| django/tests/otto/test\_utils\_middleware.py                                             |       35 |        0 |    100% |           |
| django/tests/settings.py                                                                 |        0 |        0 |    100% |           |
| django/tests/template\_wizard/test\_template\_wizard\_views.py                           |       19 |        0 |    100% |           |
| django/tests/text\_extractor/test\_tasks.py                                              |       39 |        0 |    100% |           |
| django/tests/text\_extractor/test\_utils.py                                              |      106 |        0 |    100% |           |
| django/tests/text\_extractor/test\_views.py                                              |       95 |        2 |     98% |  150, 161 |
| django/text\_extractor/\_\_init\_\_.py                                                   |        0 |        0 |    100% |           |
| django/text\_extractor/admin.py                                                          |        1 |        1 |      0% |         1 |
| django/text\_extractor/apps.py                                                           |       11 |        1 |     91% |        21 |
| django/text\_extractor/migrations/0001\_initial.py                                       |        6 |        0 |    100% |           |
| django/text\_extractor/migrations/\_\_init\_\_.py                                        |        0 |        0 |    100% |           |
| django/text\_extractor/models.py                                                         |       17 |        1 |     94% |        28 |
| django/text\_extractor/tasks.py                                                          |       18 |        2 |     89% |     34-35 |
| django/text\_extractor/urls.py                                                           |        4 |        0 |    100% |           |
| django/text\_extractor/utils.py                                                          |      211 |       42 |     80% |57-80, 115-116, 164-166, 184, 295-297, 351-355, 362-363, 369, 375-379 |
| django/text\_extractor/views.py                                                          |      108 |       21 |     81% |41, 59-74, 84, 98-106, 119-125, 142, 146, 163, 173, 193-194 |
|                                                                                **TOTAL** | **10475** | **1235** | **88%** |           |


## Setup coverage badge

Below are examples of the badges you can use in your main branch `README` file.

### Direct image

[![Coverage badge](https://raw.githubusercontent.com/justicecanada/otto/python-coverage-comment-action-data/badge.svg)](https://htmlpreview.github.io/?https://github.com/justicecanada/otto/blob/python-coverage-comment-action-data/htmlcov/index.html)

This is the one to use if your repository is private or if you don't want to customize anything.

### [Shields.io](https://shields.io) Json Endpoint

[![Coverage badge](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/justicecanada/otto/python-coverage-comment-action-data/endpoint.json)](https://htmlpreview.github.io/?https://github.com/justicecanada/otto/blob/python-coverage-comment-action-data/htmlcov/index.html)

Using this one will allow you to [customize](https://shields.io/endpoint) the look of your badge.
It won't work with private repositories. It won't be refreshed more than once per five minutes.

### [Shields.io](https://shields.io) Dynamic Badge

[![Coverage badge](https://img.shields.io/badge/dynamic/json?color=brightgreen&label=coverage&query=%24.message&url=https%3A%2F%2Fraw.githubusercontent.com%2Fjusticecanada%2Fotto%2Fpython-coverage-comment-action-data%2Fendpoint.json)](https://htmlpreview.github.io/?https://github.com/justicecanada/otto/blob/python-coverage-comment-action-data/htmlcov/index.html)

This one will always be the same color. It won't work for private repos. I'm not even sure why we included it.

## What is that?

This branch is part of the
[python-coverage-comment-action](https://github.com/marketplace/actions/python-coverage-comment)
GitHub Action. All the files in this branch are automatically generated and may be
overwritten at any moment.