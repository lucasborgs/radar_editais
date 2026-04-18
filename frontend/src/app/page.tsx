"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { loadProfileFromStorage } from "@/types/profile";

export default function Home() {
  const router = useRouter();

  useEffect(() => {
    const profile = loadProfileFromStorage();
    router.replace(profile ? "/dashboard" : "/onboarding");
  }, [router]);

  return null;
}
