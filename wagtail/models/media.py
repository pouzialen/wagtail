from django.conf import settings
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from treebeard.mp_tree import MP_Node

from wagtail.query import TreeQuerySet
from wagtail.search import index

from .view_restrictions import BaseViewRestriction


class CollectionQuerySet(TreeQuerySet):
    def get_indented_choices(self):
        """
        Return a list of (id, label) tuples for use as a list of choices in a collection chooser
        dropdown, where the label is formatted with get_indented_name to provide a tree layout.
        The indent level is chosen to place the minimum-depth collection at indent 0.
        """
        min_depth = self.aggregate(models.Min("depth"))["depth__min"] or 2
        return [
            (collection.pk, collection.get_indented_name(min_depth, html=True))
            for collection in self
        ]


class BaseCollectionManager(models.Manager):
    def get_queryset(self):
        return CollectionQuerySet(self.model).order_by("path")


CollectionManager = BaseCollectionManager.from_queryset(CollectionQuerySet)


class CollectionViewRestriction(BaseViewRestriction):
    collection = models.ForeignKey(
        "Collection",
        verbose_name=_("collection"),
        related_name="view_restrictions",
        on_delete=models.CASCADE,
    )

    passed_view_restrictions_session_key = "passed_collection_view_restrictions"

    class Meta:
        verbose_name = _("collection view restriction")
        verbose_name_plural = _("collection view restrictions")


class Collection(MP_Node):
    """
    A location in which resources such as images and documents can be grouped
    """

    name = models.CharField(max_length=255, verbose_name=_("name"))

    objects = CollectionManager()
    # Tell treebeard to order Collections' paths such that they are ordered by name at each level.
    node_order_by = ["name"]

    def __str__(self):
        return self.name

    def get_ancestors(self, inclusive=False):
        return Collection.objects.ancestor_of(self, inclusive)

    def get_descendants(self, inclusive=False):
        return Collection.objects.descendant_of(self, inclusive)

    def get_siblings(self, inclusive=True):
        return Collection.objects.sibling_of(self, inclusive)

    def get_next_siblings(self, inclusive=False):
        return self.get_siblings(inclusive).filter(path__gte=self.path).order_by("path")

    def get_prev_siblings(self, inclusive=False):
        return (
            self.get_siblings(inclusive).filter(path__lte=self.path).order_by("-path")
        )

    def get_view_restrictions(self):
        """Return a query set of all collection view restrictions that apply to this collection"""
        return CollectionViewRestriction.objects.filter(
            collection__in=self.get_ancestors(inclusive=True)
        )

    def get_indented_name(self, indentation_start_depth=2, html=False):
        """
        Renders this Collection's name as a formatted string that displays its hierarchical depth via indentation.
        If indentation_start_depth is supplied, the Collection's depth is rendered relative to that depth.
        indentation_start_depth defaults to 2, the depth of the first non-Root Collection.
        Pass html=True to get an HTML representation, instead of the default plain-text.

        Example text output: "    ↳ Pies"
        Example HTML output: "&nbsp;&nbsp;&nbsp;&nbsp;&#x21b3 Pies"
        """
        display_depth = self.depth - indentation_start_depth
        # A Collection with a display depth of 0 or less (Root's can be -1), should have no indent.
        if display_depth <= 0:
            return self.name

        # Indent each level of depth by 4 spaces (the width of the ↳ character in our admin font), then add ↳
        # before adding the name.
        if html:
            # NOTE: &#x21b3 is the hex HTML entity for ↳.
            return format_html(
                "{indent}{icon} {name}",
                indent=mark_safe("&nbsp;" * 4 * display_depth),
                icon=mark_safe("&#x21b3"),
                name=self.name,
            )
        # Output unicode plain-text version
        return "{}↳ {}".format(" " * 4 * display_depth, self.name)

    class Meta:
        verbose_name = _("collection")
        verbose_name_plural = _("collections")


def get_root_collection_id():
    return Collection.get_first_root_node().id


class CollectionMember(models.Model):
    """
    Base class for models that are categorised into collections
    """

    collection = models.ForeignKey(
        Collection,
        default=get_root_collection_id,
        verbose_name=_("collection"),
        related_name="+",
        on_delete=models.CASCADE,
    )

    search_fields = [
        index.FilterField("collection"),
    ]

    class Meta:
        abstract = True


class GroupCollectionPermissionManager(models.Manager):
    def get_by_natural_key(self, group, collection, permission):
        return self.get(group=group, collection=collection, permission=permission)


class GroupCollectionPermission(models.Model):
    """
    A rule indicating that a group has permission for some action (e.g. "create document")
    within a specified collection.
    """

    group = models.ForeignKey(
        Group,
        verbose_name=_("group"),
        related_name="collection_permissions",
        on_delete=models.CASCADE,
    )
    collection = models.ForeignKey(
        Collection,
        verbose_name=_("collection"),
        related_name="group_permissions",
        on_delete=models.CASCADE,
    )
    permission = models.ForeignKey(
        Permission, verbose_name=_("permission"), on_delete=models.CASCADE
    )

    def __str__(self):
        return "Group %d ('%s') has permission '%s' on collection %d ('%s')" % (
            self.group.id,
            self.group,
            self.permission,
            self.collection.id,
            self.collection,
        )

    def natural_key(self):
        return (self.group, self.collection, self.permission)

    objects = GroupCollectionPermissionManager()

    class Meta:
        unique_together = ("group", "collection", "permission")
        verbose_name = _("group collection permission")
        verbose_name_plural = _("group collection permissions")


class UploadedFile(models.Model):
    """
    Temporary storage for media fields uploaded through the multiple image/document uploader.
    When validation rules (e.g. required metadata fields) prevent creating an Image/Document object from the file alone.
    In this case, the file is stored against this model, to be turned into an Image/Document object once the full form
    has been filled in.
    """

    for_content_type = models.ForeignKey(
        ContentType,
        verbose_name=_("for content type"),
        related_name="uploads",
        on_delete=models.CASCADE,
        null=True,
    )
    file = models.FileField(upload_to="wagtail_uploads", max_length=200)
    uploaded_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("uploaded by user"),
        null=True,
        blank=True,
        editable=False,
        on_delete=models.SET_NULL,
    )
